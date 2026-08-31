from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
import re
from unittest.mock import patch
from .models import AuditLog, ChatMessage, Conversation, CropDiagnosis, Delivery, DeliveryEvidence, DeliveryLocationUpdate, DeliveryQuote, DiagnosisEscalation, DiagnosisReport, FavouriteListing, HistoricalMarketPrice, Listing, Notification, OperationalEvent, Order, OrganizationMember, OrderStatusHistory, Organization, PlatformSetting, RecentlyViewedListing, SavedSearch, ServiceIncident, Subscription, TeamApprovalRequest, TraceabilityAudit, TraceabilityBatch, TraceabilityEvent, TransporterProfile, USSDCredential, User, WantedListing, WholesalePriceTier
from .traceability import verify_chain

class ApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="farmer", email="farmer@example.mw", password="strong-pass-123", user_type="farmer")
        self.client = APIClient(enforce_csrf_checks=True)

    def test_service_root_reports_online(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "online")
        self.assertTrue(response["X-Correlation-ID"])
        self.assertIn("app;dur=", response["Server-Timing"])

    def test_historical_market_prices_are_filtered_and_attributed(self):
        from datetime import date
        HistoricalMarketPrice.objects.create(
            source_version=date(2026, 8, 24), region="Central Region", district="Lilongwe",
            market="Area 23", geo_id="area-23", latitude="-14.03", longitude="33.49",
            price_date=date(2026, 8, 1), crop="maize", closing_price="1267.82",
            trust_score="9.0", data_coverage="35.46", recent_data_coverage="57.55",
            index_confidence_score="0.97", spatially_interpolated=True,
        )
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/advisory/market-data/history/?crop=maize&market=Area%2023")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pagination"]["total"], 1)
        self.assertEqual(response.data["results"][0]["market"], "Area 23")
        self.assertTrue(response.data["results"][0]["spatially_interpolated"])
        self.assertEqual(response.data["source"]["dataset"], "MWI_2021_RTFP_v02_M")
        self.assertIn("not live quotes", response.data["methodology_notice"])

    def test_crop_diagnosis_requires_consent_strips_metadata_and_supports_safety_workflow(self):
        import io
        from unittest.mock import patch
        from PIL import Image
        from .diagnosis import CONSENT_VERSION, prepare_image

        source = io.BytesIO()
        image = Image.new("RGB", (320, 300), "green")
        exif = Image.Exif(); exif[306] = "2026:08:30 12:00:00"
        image.save(source, format="JPEG", exif=exif)
        raw = source.getvalue()
        cleaned, fingerprint = prepare_image(SimpleUploadedFile("leaf.jpg", raw, content_type="image/jpeg"))
        self.assertEqual(len(fingerprint), 64)
        with Image.open(io.BytesIO(cleaned)) as sanitized:
            self.assertFalse(sanitized.getexif())

        self.client.force_authenticate(self.user)
        denied = self.client.post("/api/advisory/diagnoses/", {"image": SimpleUploadedFile("leaf.jpg", raw, content_type="image/jpeg")}, format="multipart")
        self.assertEqual(denied.status_code, 400)

        provider_result = ("provider-token", {"crops": [{"name": "corn", "scientific_name": "Zea mays", "confidence": 91.2, "type": "", "severity": ""}], "possibilities": [{"name": "fall armyworm", "scientific_name": "Spodoptera frugiperda", "confidence": 71.4, "type": "pest", "severity": "high"}], "warning": "Not confirmed."})
        with patch("core.views.identify", return_value=provider_result):
            created = self.client.post("/api/advisory/diagnoses/", {"image": SimpleUploadedFile("leaf.jpg", raw, content_type="image/jpeg"), "crop": "maize", "consent": "true", "consent_version": CONSENT_VERSION}, format="multipart")
        self.assertEqual(created.status_code, 201)
        diagnosis_id = created.data["id"]
        diagnosis = CropDiagnosis.objects.get(id=diagnosis_id)
        self.assertEqual(diagnosis.provider_reference, "provider-token")
        self.assertNotIn("treatment", str(diagnosis.results).lower())

        report = self.client.post(f"/api/advisory/diagnoses/{diagnosis_id}/reports/", {"category": "harmful_advice", "details": "This result could lead to unsafe treatment."}, format="json")
        escalation = self.client.post(f"/api/advisory/diagnoses/{diagnosis_id}/escalations/", {"reason": "Please have an extension worker verify this result."}, format="json")
        self.assertEqual(report.status_code, 201); self.assertEqual(escalation.status_code, 201)
        self.assertEqual(DiagnosisReport.objects.count(), 1); self.assertEqual(DiagnosisEscalation.objects.count(), 1)

        with patch("core.views.delete_remote", return_value=True):
            deleted = self.client.delete(f"/api/advisory/diagnoses/{diagnosis_id}/")
        self.assertEqual(deleted.status_code, 200)
        diagnosis.refresh_from_db()
        self.assertEqual(diagnosis.results, {}); self.assertEqual(diagnosis.status, "deleted")

    def test_geocoding_is_malawi_bounded_cached_attributed_and_signed(self):
        import json
        from unittest.mock import patch
        from .geocoding import read_selection
        payload = [{"display_name": "Area 23, Lilongwe, Malawi", "lat": "-14.0300", "lon": "33.4900", "osm_type": "node", "osm_id": 123, "type": "suburb", "address": {"state_district": "Lilongwe"}}]
        class ProviderResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(payload).encode("utf-8")
        self.client.force_authenticate(self.user)
        with patch("core.geocoding.urlopen", return_value=ProviderResponse()) as provider:
            first = self.client.get("/api/locations/search/?q=Area%2023")
            second = self.client.get("/api/locations/search/?q=Area%2023")
        self.assertEqual(first.status_code, 200); self.assertEqual(second.status_code, 200)
        self.assertEqual(provider.call_count, 1); self.assertFalse(first.data["cached"]); self.assertTrue(second.data["cached"])
        self.assertEqual(first.data["country"], "Malawi"); self.assertIn("OpenStreetMap", first.data["attribution"])
        selection = read_selection(first.data["results"][0]["selection_token"])
        self.assertEqual(selection["osm_reference"], "N123")
        with patch("core.geocoding.urlopen", return_value=ProviderResponse()):
            throttled = self.client.get("/api/locations/search/?q=Blantyre%20CBD")
        self.assertEqual(throttled.status_code, 429)

    @override_settings(PAYMENT_WEBHOOK_SECRET="webhook-test-secret")
    def test_payment_webhook_signature_monitoring_and_public_status(self):
        import hashlib, hmac
        body = b'{"event":"settled","reference":"provider-ref"}'
        signature = hmac.new(b"webhook-test-secret", body, hashlib.sha256).hexdigest()
        accepted = self.client.generic("POST", "/api/payments/webhooks/test-provider/", body, content_type="application/json", HTTP_X_WEBHOOK_SIGNATURE=signature, HTTP_X_CORRELATION_ID="payment-correlation")
        self.assertEqual(accepted.status_code, 200)
        rejected = self.client.generic("POST", "/api/payments/webhooks/test-provider/", body, content_type="application/json", HTTP_X_WEBHOOK_SIGNATURE="wrong")
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(OperationalEvent.objects.filter(category="payment_webhook", status="accepted").count(), 1)
        ServiceIncident.objects.create(title="Payment delays", service="payments", message="Provider callbacks are delayed.")
        status_response = self.client.get("/api/status/")
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.data["overall"], "degraded")

    @override_settings(PAYMENT_WEBHOOK_SECRET="paychangu-webhook-secret")
    def test_paychangu_webhook_uses_signature_header(self):
        import hashlib, hmac
        body = b'{"event_type":"checkout.payment","status":"success","tx_ref":"MC-TEST-001"}'
        signature = hmac.new(b"paychangu-webhook-secret", body, hashlib.sha256).hexdigest()
        accepted = self.client.generic(
            "POST", "/api/payments/webhooks/paychangu/", body,
            content_type="application/json", HTTP_SIGNATURE=signature,
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.data["received"])

    def test_formal_log_redaction_and_backup_restoration_verification(self):
        import io, sqlite3, tempfile
        from django.core.management import call_command
        from .observability import redact
        payload = redact({"password": "secret", "phone": "+265999123456", "nested": {"authorization": "Bearer secret"}})
        self.assertEqual(payload["password"], "[REDACTED]")
        self.assertNotIn("999123456", payload["phone"])
        with tempfile.TemporaryDirectory() as directory:
            backup = __import__("pathlib").Path(directory) / "backup.sqlite3"; connection = sqlite3.connect(backup)
            for table in ["django_migrations", "core_user", "core_order", "core_listing"]: connection.execute(f'CREATE TABLE "{table}" (id integer primary key)')
            connection.commit(); connection.close(); output = io.StringIO(); call_command("verify_backup", str(backup), stdout=output)
            self.assertIn("passed", output.getvalue())

    def test_login_uses_session_cookie_without_returning_tokens(self):
        csrf = self.client.get("/api/csrf/").data["csrfToken"]
        response = self.client.post("/api/auth/login/", {"identifier": "farmer@example.mw", "password": "strong-pass-123"}, format="json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("tokens", response.data)
        self.assertIn("sessionid", response.cookies)

    def test_listing_requires_authentication(self):
        response = self.client.post("/api/marketplace/listings/", {"name": "Maize"}, format="json")
        self.assertIn(response.status_code, [401, 403])

    @override_settings(USSD_SERVICE_KEY="ussd-test",USSD_ALLOWED_IPS=["127.0.0.1"],SUPPORT_PHONE="+265111")
    def test_ussd_cached_services_and_pin_privacy_acknowledgement(self):
        Listing.objects.create(seller=self.user,name="Maize",description="Grade A",price="1000",quantity=10,category="maize",unit="kg",approval_status="approved",is_active=True)
        headers={"HTTP_X_USSD_SERVICE_KEY":"ussd-test"}
        first=self.client.get("/api/ussd/services/prices/",**headers);second=self.client.get("/api/ussd/services/prices/",**headers)
        self.assertEqual(first.status_code,200);self.assertFalse(first.data["cached"]);self.assertTrue(second.data["cached"])
        self.assertEqual(self.client.get("/api/ussd/services/support/",**headers).data["phone"],"+265111")
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post("/api/ussd/credentials/",{"pin":"1234"},format="json").status_code,400)
        created=self.client.post("/api/ussd/credentials/",{"pin":"1234","privacy_ack":True},format="json")
        self.assertEqual(created.status_code,200);self.assertTrue(USSDCredential.objects.get(user=self.user).verify("1234"))

    def test_contact_is_persisted(self):
        response = self.client.post("/api/contact/", {"name": "Alick", "email": "a@example.mw", "subject": "Help", "message": "Please contact me."}, format="json")
        self.assertEqual(response.status_code, 201)

    def test_cooperative_can_register_to_buy_and_sell(self):
        csrf = self.client.get("/api/csrf/").data["csrfToken"]
        response = self.client.post("/api/auth/register/", {
            "username": "central_coop", "email": "coop@example.mw", "phone": "+265999123456",
            "password": "strong-coop-pass-123", "user_type": "organization", "account_type": "cooperative", "trading_mode": "both",
            "organization": {"legal_name": "Central Farmers Cooperative", "registration_number": "COOP-001", "representative_name": "Mary Phiri", "representative_role": "Chairperson", "business_size": "medium", "member_count": 120, "address": "Lilongwe"},
        }, format="json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(username="central_coop")
        self.assertTrue(user.can_buy)
        self.assertTrue(user.can_sell)
        self.assertEqual(Organization.objects.get(owner=user).verification_status, "pending")

    def test_buy_only_account_cannot_create_listing(self):
        buyer = User.objects.create_user(username="buy_only", email="buy@example.mw", password="strong-pass-123", can_buy=True, can_sell=False)
        self.client.force_authenticate(buyer)
        response = self.client.post("/api/marketplace/listings/", {"name": "Seed", "description": "Certified seed", "price": "1000", "quantity": 2, "category": "seed"}, format="json")
        self.assertEqual(response.status_code, 403)

    @override_settings(PAYMENTS_ENABLED=True, PAYMENT_PROVIDER="paychangu")
    @__import__("unittest.mock", fromlist=["patch"]).patch("core.views.initialize_checkout", return_value=("https://checkout.paychangu.com/test", "MC-TEST"))
    def test_checkout_reserves_stock_and_closes_sold_out_listing(self, _initialize_checkout):
        seller = User.objects.create_user(username="seller", email="seller@example.mw", password="strong-pass-123", can_sell=True)
        listing = Listing.objects.create(seller=seller, name="Maize", description="50kg bag", price="25000", quantity=2, category="produce", approval_status="approved")
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/payments/checkout-sessions/", {"payment_method": "bank_transfer", "items": [{"product_id": listing.id, "quantity": 2}]}, format="json")
        self.assertEqual(response.status_code, 201)
        listing.refresh_from_db()
        self.assertEqual(listing.quantity, 0)
        self.assertFalse(listing.is_active)

    @override_settings(PAYMENTS_ENABLED=True, PAYMENT_PROVIDER="paychangu")
    def test_checkout_rejects_duplicate_listing_rows(self):
        seller = User.objects.create_user(username="seller2", email="seller2@example.mw", password="strong-pass-123", can_sell=True)
        listing = Listing.objects.create(seller=seller, name="Beans", description="Dry beans", price="5000", quantity=5, category="produce")
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/payments/checkout-sessions/", {"payment_method": "bank_transfer", "items": [{"product_id": listing.id, "quantity": 1}, {"product_id": listing.id, "quantity": 1}]}, format="json")
        self.assertEqual(response.status_code, 400)
        listing.refresh_from_db()
        self.assertEqual(listing.quantity, 5)

    def test_seller_cannot_mark_pending_order_as_paid(self):
        seller = User.objects.create_user(username="seller3", email="seller3@example.mw", password="strong-pass-123", can_sell=True)
        buyer = User.objects.create_user(username="buyer3", email="buyer3@example.mw", password="strong-pass-123")
        listing = Listing.objects.create(seller=seller, name="Rice", description="Local rice", price="9000", quantity=3, category="produce")
        order = Order.objects.create(buyer=buyer, payment_method="bank_transfer")
        order.items.create(listing=listing, quantity=1, unit_price=listing.price)
        self.client.force_authenticate(seller)
        response = self.client.patch(f"/api/marketplace/orders/{order.id}/status/", {"status": "paid"}, format="json")
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")

    def test_cancelling_order_restores_reserved_stock(self):
        seller = User.objects.create_user(username="seller4", email="seller4@example.mw", password="strong-pass-123", can_sell=True)
        listing = Listing.objects.create(seller=seller, name="Peanuts", description="Shelled", price="7000", quantity=0, category="produce", is_active=False)
        buyer = User.objects.create_user(username="buyer4", email="buyer4@example.mw", password="strong-pass-123")
        order = Order.objects.create(buyer=buyer, payment_method="bank_transfer", status="paid", acceptance_deadline=__import__("django.utils.timezone", fromlist=["now"]).now() + __import__("datetime").timedelta(hours=2))
        order.items.create(listing=listing, quantity=2, unit_price=listing.price)
        self.client.force_authenticate(seller)
        response = self.client.patch(f"/api/marketplace/orders/{order.id}/status/", {"status": "cancelled", "reason": "Buyer cancelled before acceptance."}, format="json")
        self.assertEqual(response.status_code, 200)
        listing.refresh_from_db()
        self.assertEqual(listing.quantity, 2)
        self.assertTrue(listing.is_active)

    def test_seller_lifecycle_records_history_and_notifies_buyer(self):
        seller = User.objects.create_user(username="lifecycle_seller", email="life-seller@example.mw", password="strong-pass-123", can_sell=True)
        buyer = User.objects.create_user(username="lifecycle_buyer", email="life-buyer@example.mw", password="strong-pass-123")
        listing = Listing.objects.create(seller=seller, name="Cassava", description="Fresh", price="4000", quantity=4, category="produce")
        order = Order.objects.create(buyer=buyer, payment_method="bank_transfer", status="paid", acceptance_deadline=__import__("django.utils.timezone", fromlist=["now"]).now() + __import__("datetime").timedelta(hours=2))
        order.items.create(listing=listing, quantity=2, unit_price=listing.price)
        self.client.force_authenticate(seller)
        for state in ["accepted", "packed", "dispatched"]:
            self.assertEqual(self.client.patch(f"/api/marketplace/orders/{order.id}/status/", {"status": state}, format="json").status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "dispatched")
        self.assertEqual(OrderStatusHistory.objects.filter(order=order).count(), 3)
        self.assertTrue(Notification.objects.filter(user=buyer, type="order").exists())

    def test_buyer_confirms_delivery_after_evidence(self):
        seller = User.objects.create_user(username="delivery_seller", email="delivery-seller@example.mw", password="strong-pass-123", can_sell=True)
        buyer = User.objects.create_user(username="delivery_buyer", email="delivery-buyer@example.mw", password="strong-pass-123")
        listing = Listing.objects.create(seller=seller, name="Potatoes", description="Bag", price="8000", quantity=2, category="produce")
        order = Order.objects.create(buyer=buyer, payment_method="bank_transfer", status="delivered")
        item = order.items.create(listing=listing, quantity=2, unit_price=listing.price)
        DeliveryEvidence.objects.create(order=order, created_by=seller, evidence_type="delivery", reference="POD-001")
        self.client.force_authenticate(buyer)
        response = self.client.patch(f"/api/marketplace/orders/{order.id}/status/", {"status": "completed"}, format="json")
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.fulfilled_quantity, item.quantity)

    def test_traceability_event_requires_stage_and_description(self):
        batch = TraceabilityBatch.objects.create(owner=self.user, batch_code="BATCH-VALIDATION", product="Maize", quantity="2 bags")
        self.client.force_authenticate(self.user)
        response = self.client.post(f"/api/traceability/batches/{batch.id}/events/", {"stage": "", "description": ""}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(batch.events.count(), 0)

    def test_traceability_events_chain_evidence_and_corrections(self):
        batch = TraceabilityBatch.objects.create(owner=self.user, batch_code="BATCH-CHAIN", product="Maize", quantity="100 kg")
        self.client.force_authenticate(self.user)
        first = self.client.post(f"/api/traceability/batches/{batch.id}/events/", {"event_type": "harvest", "stage": "harvested", "description": "Harvest weighed at the farm.", "location": "Mchinji", "quantity": "100", "unit": "kg", "evidence": SimpleUploadedFile("receipt.pdf", b"weighing receipt", content_type="application/pdf")}, format="multipart")
        self.assertEqual(first.status_code, 201)
        correction = self.client.post(f"/api/traceability/batches/{batch.id}/events/", {"event_type": "correction", "stage": "harvested", "description": "Corrected calibrated scale reading.", "location": "Mchinji", "quantity": "99.5", "unit": "kg", "corrects": first.data["id"]}, format="multipart")
        self.assertEqual(correction.status_code, 201)
        events = list(batch.events.order_by("occurred_at", "id"))
        self.assertEqual(events[1].previous_hash, events[0].event_hash)
        self.assertEqual(events[1].corrects_id, events[0].id)
        self.assertTrue(events[0].evidence.exists())
        self.assertEqual(TraceabilityAudit.objects.filter(batch=batch).count(), 2)
        self.assertEqual(verify_chain(batch), (True, None))

    def test_traceability_chain_detects_database_tampering(self):
        batch = TraceabilityBatch.objects.create(owner=self.user, batch_code="BATCH-TAMPER", product="Rice", quantity="10 bags")
        self.client.force_authenticate(self.user)
        created = self.client.post(f"/api/traceability/batches/{batch.id}/events/", {"event_type": "packed", "stage": "packed", "description": "Packed into sealed bags.", "quantity": "10", "unit": "bags"}, format="json")
        self.assertEqual(created.status_code, 201)
        TraceabilityEvent.objects.filter(id=created.data["id"]).update(description="Altered after creation")
        valid, broken_id = verify_chain(batch)
        self.assertFalse(valid)
        self.assertEqual(broken_id, created.data["id"])

    def test_conversation_messages_are_private_and_persisted(self):
        buyer = User.objects.create_user(username="buyer2", email="buyer2@example.mw", password="strong-pass-123")
        outsider = User.objects.create_user(username="outsider", email="outsider@example.mw", password="strong-pass-123")
        conversation = Conversation.objects.create(); conversation.participants.add(self.user, buyer)
        self.client.force_authenticate(self.user)
        sent = self.client.post(f"/api/messages/conversations/{conversation.id}/messages/", {"text": "Is the equipment available?"}, format="json")
        self.assertEqual(sent.status_code, 201)
        self.assertTrue(ChatMessage.objects.filter(conversation=conversation, sender=self.user).exists())
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(f"/api/messages/conversations/{conversation.id}/messages/").status_code, 404)

    def test_notification_read_state_and_preferences(self):
        self.client.force_authenticate(self.user)
        notification = Notification.objects.create(user=self.user, type="order", title="Order paid", message="Payment confirmed")
        self.assertEqual(self.client.post(f"/api/notifications/{notification.id}/read/").status_code, 204)
        notification.refresh_from_db(); self.assertIsNotNone(notification.read_at)
        saved = self.client.put("/api/users/notifications", {"emailOrders": False, "pushMessages": True}, format="json")
        self.assertEqual(saved.status_code, 200)
        self.assertFalse(self.client.get("/api/users/notifications").data["emailOrders"])

    @patch("core.crop_planning.get_weather")
    def test_crop_planning_combines_attributed_sources_without_fake_score(self, weather):
        from datetime import date
        weather.return_value = {"collected_at": "2026-08-31T08:00:00+00:00", "stale": False, "current": {"temperature_c": 24}, "forecast": []}
        self.client.force_authenticate(self.user)
        HistoricalMarketPrice.objects.create(source_version=date(2026, 8, 24), region="Central", district="Lilongwe", market="Area 23", geo_id="planning-area-23", price_date=date(2026, 8, 1), crop="maize", closing_price="1267.82")
        response = self.client.post("/api/advisory/crop-planning/", {"task": "crop_planning", "location": "Lilongwe", "soilType": "Loamy", "season": "Rainy", "preferredCrop": "maize"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["recommendations"][0]["crop"], "Maize")
        self.assertNotIn("suitability", response.data["recommendations"][0])
        self.assertEqual([source["name"] for source in response.data["sources"]], ["Open-Meteo", "World Bank Microdata Library", "MlimiConnect safety-reviewed crop planning rules"])
        self.assertIn("no generative AI", response.data["method"])

    @override_settings(USSD_SERVICE_KEY="test-service-secret")
    def test_ussd_authentication_requires_service_key_and_hashed_pin(self):
        self.user.phone = "+265999000000"
        self.user.save(update_fields=["phone"])
        credential = USSDCredential(user=self.user)
        credential.set_pin("1234")
        credential.save()
        denied = self.client.post("/api/ussd/authenticate", {"phone": self.user.phone, "pin": "1234"}, format="json")
        self.assertEqual(denied.status_code, 403)
        wrong = self.client.post("/api/ussd/authenticate", {"phone": self.user.phone, "pin": "0000"}, format="json", HTTP_X_USSD_SERVICE_KEY="test-service-secret")
        self.assertEqual(wrong.data, {"authenticated": False})
        allowed = self.client.post("/api/ussd/authenticate", {"phone": self.user.phone, "pin": "1234"}, format="json", HTTP_X_USSD_SERVICE_KEY="test-service-secret")
        self.assertEqual(allowed.data, {"authenticated": True})

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", FRONTEND_URL="http://frontend.test")
    def test_email_password_reset_is_single_use(self):
        requested = self.client.post("/api/auth/forgot-password/", {"method": "email", "email": self.user.email}, format="json")
        self.assertEqual(requested.status_code, 200)
        body = mail.outbox[0].body
        code = re.search(r"code is (\d{6})", body).group(1)
        token = re.search(r"token=([0-9a-f-]+)", body).group(1)
        self.assertEqual(self.client.post("/api/auth/verify-reset-otp/", {"otp": code, "token": token}, format="json").status_code, 200)
        changed = self.client.post("/api/auth/reset-password/", {"otp": code, "token": token, "password": "new-strong-pass-456"}, format="json")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.post("/api/auth/reset-password/", {"otp": code, "token": token, "password": "another-pass-789"}, format="json").status_code, 400)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_account_deletion_requires_password_and_email_code(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post("/api/users/account", {"password": "wrong"}, format="json").status_code, 400)
        requested = self.client.post("/api/users/account", {"password": "strong-pass-123"}, format="json")
        self.assertEqual(requested.status_code, 200)
        code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        self.assertEqual(self.client.delete("/api/users/account", {"token": requested.data["token"], "otp": "000000"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete("/api/users/account", {"token": requested.data["token"], "otp": code}, format="json").status_code, 204)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_admin_listing_approval_records_snapshots(self):
        admin = User.objects.create_user(username="admin-one", password="admin-pass-123", user_type="admin", is_staff=True)
        listing = Listing.objects.create(seller=self.user, name="Groundnuts", description="Fresh crop", price="1500", quantity=20, category="Legumes")
        self.client.force_authenticate(admin)
        response = self.client.post("/api/admin/listings/approvals/", {"listing_id": listing.id, "decision": "approved", "reason": "Verified listing details."}, format="json")
        self.assertEqual(response.status_code, 200)
        listing.refresh_from_db()
        self.assertEqual(listing.approval_status, "approved")
        audit = AuditLog.objects.get(action="listing.approved", target_id=str(listing.id))
        self.assertEqual(audit.metadata["before"]["approval_status"], "pending")
        self.assertEqual(audit.metadata["after"]["approval_status"], "approved")

    def test_admin_can_suspend_reinstate_and_assign_role_with_audit(self):
        admin = User.objects.create_user(username="admin-two", email="admin-two@example.mw", password="admin-pass-123", user_type="admin", is_staff=True)
        target = User.objects.create_user(username="buyer-one", email="buyer-one@example.mw", password="buyer-pass-123", user_type="buyer")
        self.client.force_authenticate(admin)
        suspended = self.client.post(f"/api/admin/users/{target.id}/action/", {"action": "suspend", "reason": "Confirmed policy violation."}, format="json")
        self.assertEqual(suspended.status_code, 200)
        reinstated = self.client.post(f"/api/admin/users/{target.id}/action/", {"action": "reinstate", "reason": "Appeal successfully reviewed."}, format="json")
        self.assertEqual(reinstated.status_code, 200)
        assigned = self.client.post(f"/api/admin/users/{target.id}/role/", {"role": "farmer", "reason": "Identity and farmer role verified."}, format="json")
        self.assertEqual(assigned.status_code, 200)
        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertEqual(target.user_type, "farmer")
        self.assertTrue(AuditLog.objects.filter(actor=admin, action="user.role_assigned").exists())

    def test_admin_fee_settings_and_csv_export_are_audited(self):
        admin = User.objects.create_user(username="admin-three", password="admin-pass-123", user_type="admin", is_staff=True)
        self.client.force_authenticate(admin)
        fees = self.client.put("/api/admin/fees/", {"platform_percent": "4.5", "withdrawal_percent": "1.0", "minimum_fee": "100", "reason": "Approved annual fee review."}, format="json")
        self.assertEqual(fees.status_code, 200)
        self.assertEqual(PlatformSetting.objects.get(key="fee_configuration").value["platform_percent"], "4.5")
        export = self.client.get("/api/admin/exports/users/")
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "text/csv")
        self.assertIn("username", export.content.decode())
        self.assertTrue(AuditLog.objects.filter(actor=admin, action="data.exported", target_id="users").exists())

    def test_organization_team_permissions_shared_listing_and_approval(self):
        owner = User.objects.create_user(username="coop-owner", email="owner@coop.mw", password="owner-pass-123", account_type="cooperative", user_type="organization", can_sell=True)
        organization = Organization.objects.create(owner=owner, legal_name="Test Cooperative", registration_number="COOP-100", representative_name="Owner", representative_role="Chair", address="Lilongwe", verification_status="verified")
        OrganizationMember.objects.create(organization=organization, user=owner, role="owner", status="active", can_procure=True, can_manage_members=True, can_manage_listings=True, can_approve=True, invited_by=owner)
        delegate = User.objects.create_user(username="coop-seller", email="seller@coop.mw", password="seller-pass-123", user_type="farmer")
        approver = User.objects.create_user(username="coop-manager", email="manager@coop.mw", password="manager-pass-123", user_type="buyer")
        self.client.force_authenticate(owner)
        self.assertEqual(self.client.post("/api/organizations/me/team/", {"email": delegate.email, "role": "seller", "reason": "Approved delegated seller."}, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/organizations/me/team/", {"email": approver.email, "role": "manager", "reason": "Approved delegated manager."}, format="json").status_code, 201)
        self.client.force_authenticate(delegate)
        listing_response = self.client.post("/api/marketplace/listings/", {"name": "Coop maize", "description": "Shared harvest", "price": "5000", "quantity": 30, "category": "Grain"}, format="json")
        self.assertEqual(listing_response.status_code, 201)
        self.assertEqual(Listing.objects.get(id=listing_response.data["id"]).organization_id, organization.id)
        approval = self.client.post("/api/organizations/me/approvals/", {"action_type": "procurement_order", "payload": {"amount": 50000}}, format="json")
        self.client.force_authenticate(approver)
        decided = self.client.patch("/api/organizations/me/approvals/", {"approval_id": approval.data["id"], "decision": "approved", "reason": "Budget and supplier verified."}, format="json")
        self.assertEqual(decided.status_code, 200)
        self.assertEqual(TeamApprovalRequest.objects.get(id=approval.data["id"]).status, "approved")
        self.assertEqual(self.client.get("/api/organizations/me/report/").status_code, 200)

    def test_delivery_quotes_location_and_required_evidence(self):
        seller = User.objects.create_user(username="log-seller", email="log-seller@example.mw", password="seller-pass-123", user_type="farmer", can_sell=True)
        buyer = User.objects.create_user(username="log-buyer", email="log-buyer@example.mw", password="buyer-pass-123", user_type="buyer")
        driver = User.objects.create_user(username="log-driver", email="log-driver@example.mw", password="driver-pass-123", user_type="farmer")
        TransporterProfile.objects.create(user=driver, vehicle_type="Truck", capacity_kg=2000, license_reference="LIC-1", verification_status="verified")
        listing = Listing.objects.create(seller=seller, name="Maize", description="Crop", price="1000", quantity=20, category="Grain", approval_status="approved")
        order = Order.objects.create(buyer=buyer, status="paid", subtotal="10000", total="10000", payment_method="test")
        __import__("core.models", fromlist=["OrderItem"]).OrderItem.objects.create(order=order, listing=listing, quantity=10, unit_price="1000")
        self.client.force_authenticate(buyer)
        requested = self.client.post("/api/deliveries/requests/", {"order_id": order.id, "pickup_location": "Farm", "delivery_location": "Market", "distance_km": "12"}, format="json")
        self.assertEqual(requested.status_code, 201)
        delivery_id = requested.data["id"]
        self.client.force_authenticate(driver)
        quoted = self.client.post(f"/api/deliveries/{delivery_id}/quotes/", {"amount": "12000", "estimated_hours": 3}, format="json")
        self.assertEqual(quoted.status_code, 201)
        self.client.force_authenticate(buyer)
        accepted = self.client.patch(f"/api/deliveries/{delivery_id}/quotes/", {"quote_id": quoted.data["id"], "accept_liability": True}, format="json")
        self.assertEqual(accepted.status_code, 200)
        self.client.force_authenticate(driver)
        location = self.client.post(f"/api/deliveries/{delivery_id}/locations/", {"latitude": "-13.9626", "longitude": "33.7741", "accuracy_m": 8}, format="json")
        self.assertEqual(location.status_code, 201)
        self.assertEqual(DeliveryLocationUpdate.objects.filter(delivery_id=delivery_id).count(), 1)
        no_evidence = self.client.patch(f"/api/deliveries/{delivery_id}/status/", {"status": "picked_up"}, format="json")
        self.assertEqual(no_evidence.status_code, 400)
        photo = SimpleUploadedFile("pickup.jpg", b"jpeg-data", content_type="image/jpeg")
        proof = self.client.post(f"/api/marketplace/orders/{order.id}/delivery-evidence/", {"evidence_type": "pickup", "file": photo, "note": "Loaded at farm"}, format="multipart")
        self.assertEqual(proof.status_code, 201)
        self.assertEqual(self.client.patch(f"/api/deliveries/{delivery_id}/status/", {"status": "picked_up"}, format="json").status_code, 200)

    def test_agricultural_listing_search_filters_normalized_price_and_expiry(self):
        self.user.is_buyer_verified = True; self.user.save(update_fields=["is_buyer_verified"])
        listing = Listing.objects.create(seller=self.user, name="Certified Kilombero Rice", description="Aromatic harvest", price="100000", quantity=50, category="produce", unit="bag", pack_size="50", minimum_order=5, variety="Kilombero", grade="A", is_organic=True, available_from="2026-08-01", expiry_date="2027-01-01", approval_status="approved", latitude="-13.962600", longitude="33.774100", delivery_radius_km=100, allow_partial_fulfilment=True)
        WholesalePriceTier.objects.create(listing=listing, minimum_quantity=10, price_per_unit="90000")
        response = self.client.get("/api/marketplace/public-listings/?q=Kilombero&verified_only=true&organic=true&wholesale=true&available_on=2026-09-01&latitude=-13.96&longitude=33.77&radius_km=10")
        self.assertEqual(response.status_code, 200)
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        self.assertEqual(rows[0]["id"], listing.id)
        self.assertEqual(rows[0]["normalized_price"], "2000")
        listing.listing_expires_at = __import__("django.utils.timezone", fromlist=["now"]).now() - __import__("datetime").timedelta(minutes=1); listing.save(update_fields=["listing_expires_at"])
        expired = self.client.get("/api/marketplace/public-listings/?q=Kilombero")
        expired_rows = expired.data["results"] if isinstance(expired.data, dict) else expired.data
        self.assertEqual(expired_rows, [])

    def test_marketplace_saved_state_wanted_and_comparison_are_server_backed(self):
        second = User.objects.create_user(username="market-seller", email="market-seller@example.mw", password="seller-pass-123", user_type="farmer", can_sell=True)
        first_listing = Listing.objects.create(seller=second, name="Beans A", description="Grade A", price="2000", quantity=30, category="produce", unit="kg", approval_status="approved")
        second_listing = Listing.objects.create(seller=second, name="Beans B", description="Grade B", price="1800", quantity=40, category="produce", unit="kg", approval_status="approved")
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post("/api/marketplace/searches/saved/", {"name": "Beans nearby", "filters": {"q": "beans", "radius_km": 50}}, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/marketplace/wanted/", {"title": "Need beans", "description": "Clean dry beans", "category": "produce", "quantity": 20, "unit": "kg"}, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/marketplace/favourites/", {"listing_id": first_listing.id}, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/marketplace/recently-viewed/", {"listing_id": second_listing.id}, format="json").status_code, 201)
        self.assertEqual(self.client.get(f"/api/marketplace/compare/?ids={first_listing.id},{second_listing.id}").status_code, 200)
        self.assertTrue(SavedSearch.objects.filter(user=self.user).exists())
        self.assertTrue(WantedListing.objects.filter(buyer=self.user).exists())
        self.assertTrue(FavouriteListing.objects.filter(user=self.user).exists())
        self.assertTrue(RecentlyViewedListing.objects.filter(user=self.user).exists())

    def test_partial_fulfilment_requires_listing_permission(self):
        seller = User.objects.create_user(username="partial-seller", email="partial-seller@example.mw", password="seller-pass-123", user_type="farmer", can_sell=True)
        listing = Listing.objects.create(seller=seller, name="Soy", description="Soy", price="1000", quantity=50, category="produce", approval_status="approved", allow_partial_fulfilment=False)
        order = Order.objects.create(buyer=self.user, status="accepted", subtotal="10000", total="10000", payment_method="test")
        __import__("core.models", fromlist=["OrderItem"]).OrderItem.objects.create(order=order, listing=listing, quantity=10, unit_price="1000")
        self.client.force_authenticate(seller)
        denied = self.client.post(f"/api/marketplace/orders/{order.id}/partial-fulfilment/", {"items": [{"listing_id": listing.id, "fulfilled_quantity": 5}], "reason": "Only five units available."}, format="json")
        self.assertEqual(denied.status_code, 400)
        listing.allow_partial_fulfilment = True; listing.save(update_fields=["allow_partial_fulfilment"])
        allowed = self.client.post(f"/api/marketplace/orders/{order.id}/partial-fulfilment/", {"items": [{"listing_id": listing.id, "fulfilled_quantity": 5}], "reason": "Only five units available."}, format="json")
        self.assertEqual(allowed.status_code, 200)
