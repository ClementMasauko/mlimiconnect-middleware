from django.test import TestCase
from rest_framework.test import APIClient

from .models import HerdFlock, Listing, LiveAnimalListingDetail, LivestockAlert, LivestockProfile, Notification, TraceabilityEvent, User
from .traceability import verify_chain


class LivestockApiTests(TestCase):
    def setUp(self):
        self.farmer = User.objects.create_user(username="livestock-farmer", email="livestock@example.mw", password="pass", user_type="farmer", can_sell=True)
        self.admin = User.objects.create_superuser(username="livestock-admin", email="admin-livestock@example.mw", password="pass")
        self.client = APIClient()
        self.client.force_authenticate(self.farmer)

    def test_profile_herd_records_reminders_and_safe_advice(self):
        profile = self.client.put("/api/livestock/profile/", {"farm_name": "Mlimi Livestock", "production_system": "mixed", "species_kept": ["cattle", "chickens_layers"], "district": "Lilongwe"}, format="json")
        self.assertEqual(profile.status_code, 200)
        self.assertTrue(LivestockProfile.objects.filter(user=self.farmer).exists())

        created = self.client.post("/api/livestock/herds/", {"name": "Layer flock A", "species": "chickens_layers", "breed": "Hybrid", "purpose": "eggs", "head_count": 120}, format="json")
        self.assertEqual(created.status_code, 201)
        herd_id = created.data["id"]
        animal = self.client.post(f"/api/livestock/herds/{herd_id}/animals/", {"identifier": "RING-001", "sex": "female"}, format="json")
        health = self.client.post(f"/api/livestock/herds/{herd_id}/health-events/", {"event_type": "inspection", "occurred_on": "2026-08-30", "description": "Routine observation", "animal_id": animal.data["id"]}, format="json")
        production = self.client.post(f"/api/livestock/herds/{herd_id}/production/", {"record_type": "eggs", "recorded_on": "2026-08-30", "quantity": "96", "unit": "item"}, format="json")
        reminder = self.client.post("/api/livestock/reminders/", {"herd_id": herd_id, "vaccine_name": "Professional schedule review", "due_on": "2026-09-30"}, format="json")
        self.assertEqual(animal.status_code, 201); self.assertEqual(health.status_code, 201); self.assertEqual(production.status_code, 201); self.assertEqual(reminder.status_code, 201)
        detail = self.client.get(f"/api/livestock/herds/{herd_id}/")
        self.assertEqual(len(detail.data["animals"]), 1); self.assertEqual(len(detail.data["health_events"]), 1); self.assertEqual(len(detail.data["production_records"]), 1)
        advice = self.client.get("/api/livestock/advisory/?species=chickens_layers")
        self.assertIn("not a diagnosis", advice.data["warning"]); self.assertNotIn("dosage", " ".join(advice.data["guidance"]).lower())
        self.assertGreaterEqual(Notification.objects.filter(user=self.farmer).count(), 2)

    def test_live_animal_listing_requires_welfare_declaration_and_admin_verification(self):
        herd = HerdFlock.objects.create(owner=self.farmer, name="Goat group", species="goats", head_count=8)
        listing = Listing.objects.create(seller=self.farmer, name="Breeding goats", description="Healthy goat group", price="120000", quantity=2, category="live-animals")
        denied = self.client.post(f"/api/livestock/listings/{listing.id}/details/", {"herd_id": herd.id}, format="json")
        self.assertEqual(denied.status_code, 400)
        created = self.client.post(f"/api/livestock/listings/{listing.id}/details/", {"herd_id": herd.id, "welfare_declaration": True, "vaccination_summary": "Records available"}, format="json")
        self.assertEqual(created.status_code, 201)
        self.client.force_authenticate(self.admin)
        verified = self.client.post(f"/api/admin/livestock/listings/{listing.id}/verification/", {"decision": "verified", "reason": "Documents reviewed"}, format="json")
        self.assertEqual(verified.status_code, 200)
        detail = LiveAnimalListingDetail.objects.get(listing=listing); listing.refresh_from_db()
        self.assertEqual(detail.verification_status, "verified"); self.assertEqual(listing.approval_status, "approved")

    def test_extended_records_traceability_profitability_alerts_and_restrictions(self):
        herd_response = self.client.post("/api/livestock/herds/", {"name": "Dairy herd", "species": "cattle", "head_count": 5}, format="json")
        herd_id = herd_response.data["id"]; herd = HerdFlock.objects.get(id=herd_id)
        animal = self.client.post(f"/api/livestock/herds/{herd_id}/animals/", {"identifier": "MW-COW-001", "sex": "female", "acquisition_type": "birth", "acquisition_date": "2026-01-01", "breeding_status": "breeding"}, format="json")
        weight = self.client.post(f"/api/livestock/animals/{animal.data['id']}/weights/", {"recorded_on": "2026-08-30", "weight_kg": "315.5"}, format="json")
        breeding = self.client.post(f"/api/livestock/herds/{herd_id}/breeding/", {"animal_id": animal.data["id"], "event_type": "pregnancy_check", "occurred_on": "2026-08-30", "outcome": "Positive professional check"}, format="json")
        expense = self.client.post(f"/api/livestock/herds/{herd_id}/financial-records/", {"record_type": "expense", "category": "feed", "amount": "25000", "occurred_on": "2026-08-30"}, format="json")
        income = self.client.post(f"/api/livestock/herds/{herd_id}/financial-records/", {"record_type": "income", "category": "milk", "amount": "40000", "occurred_on": "2026-08-30"}, format="json")
        movement = self.client.post(f"/api/livestock/herds/{herd_id}/movements/", {"event_type": "movement", "location": "Lilongwe", "quantity": 5, "description": "Moved to inspected holding pen."}, format="json")
        self.assertEqual(weight.status_code, 201); self.assertEqual(breeding.status_code, 201); self.assertEqual(expense.status_code, 201); self.assertEqual(income.status_code, 201); self.assertEqual(movement.status_code, 201)
        detail = self.client.get(f"/api/livestock/herds/{herd_id}/")
        self.assertEqual(detail.data["profitability"]["net"], 15000)
        self.assertGreaterEqual(TraceabilityEvent.objects.filter(batch=herd.traceability_batch).count(), 3)
        self.assertTrue(verify_chain(herd.traceability_batch)[0])

        self.client.post(f"/api/livestock/herds/{herd_id}/production/", {"record_type": "milk", "recorded_on": "2026-08-29", "quantity": "100", "unit": "litre"}, format="json")
        self.client.post(f"/api/livestock/herds/{herd_id}/production/", {"record_type": "milk", "recorded_on": "2026-08-30", "quantity": "50", "unit": "litre"}, format="json")
        self.assertTrue(LivestockAlert.objects.filter(herd=herd, alert_type="production_drop").exists())

        self.client.force_authenticate(self.admin)
        restriction = self.client.post("/api/admin/livestock/operations/", {"action": "restriction", "species": "cattle", "reason": "Official movement control test", "starts_on": "2026-08-01", "source_name": "Test veterinary authority"}, format="json")
        self.assertEqual(restriction.status_code, 201)
        self.client.force_authenticate(self.farmer)
        listing = Listing.objects.create(seller=self.farmer, name="Restricted cattle", description="Test", price="100", quantity=1, category="cattle")
        blocked = self.client.post(f"/api/livestock/listings/{listing.id}/details/", {"herd_id": herd.id, "welfare_declaration": True}, format="json")
        self.assertEqual(blocked.status_code, 409)
