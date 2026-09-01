from django.contrib.auth import authenticate, password_validation
from django.db import transaction
from rest_framework import serializers
from .models import ChatMessage, ContactMessage, Conversation, EmailVerificationRequest, Listing, NewsletterSubscription, Notification, Order, OrderItem, OrderReview, OrderStatusHistory, Organization, OrganizationMember, Subscription, TraceabilityBatch, TraceabilityEvent, User, WholesalePriceTier
from .communications import deliver_security_code

class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = ["plan_id", "status", "billing_cycle", "renews_at", "enabled_features"]

class UserSerializer(serializers.ModelSerializer):
    isBuyerVerified = serializers.BooleanField(source="is_buyer_verified", read_only=True)
    organization_status = serializers.SerializerMethodField()
    subscription = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = ["id", "username", "email", "email_verified", "phone", "location", "user_type", "account_type", "can_buy", "can_sell", "organization_status", "isBuyerVerified", "subscription"]
        read_only_fields = ["id", "user_type", "account_type", "can_buy", "can_sell", "isBuyerVerified"]
    def get_organization_status(self, obj):
        if obj.account_type == "individual": return None
        return obj.organization.verification_status if hasattr(obj, "organization") else "pending"
    def get_subscription(self, obj):
        subscription, _ = Subscription.objects.get_or_create(user=obj)
        return SubscriptionSerializer(subscription).data

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    trading_mode = serializers.ChoiceField(choices=["buy", "sell", "both"], write_only=True)
    organization = serializers.DictField(write_only=True, required=False)
    class Meta:
        model = User
        fields = ["username", "email", "phone", "password", "user_type", "account_type", "trading_mode", "organization"]
    def validate_password(self, value):
        password_validation.validate_password(value)
        return value
    def validate_phone(self, value):
        compact = str(value or "").replace(" ", "").replace("-", "")
        if not compact: return ""
        normalized = compact if compact.startswith("+265") else f"+265{compact[1:]}" if compact.startswith("0") else ""
        if len(normalized) != 13 or not normalized[1:].isdigit() or normalized[4] not in "789": raise serializers.ValidationError("Use a valid Malawi number such as +265999123456.")
        return normalized
    @transaction.atomic
    def create(self, validated_data):
        organization_data = validated_data.pop("organization", None)
        trading_mode = validated_data.pop("trading_mode")
        account_type = validated_data.get("account_type", "individual")
        if account_type != "individual":
            required = ["legal_name", "registration_number", "representative_name", "representative_role", "address"]
            missing = [field for field in required if not str((organization_data or {}).get(field, "")).strip()]
            if missing: raise serializers.ValidationError({"organization": f"Missing required fields: {', '.join(missing)}"})
            validated_data["user_type"] = "organization"
        validated_data["can_buy"] = trading_mode in ["buy", "both"]
        validated_data["can_sell"] = trading_mode in ["sell", "both"]
        user = User.objects.create_user(**validated_data)
        user.is_active = False
        user.save(update_fields=["is_active"])
        code = f"{__import__('secrets').randbelow(1000000):06d}"
        verification = EmailVerificationRequest(user=user, expires_at=__import__('django.utils.timezone', fromlist=['now']).now() + __import__('datetime').timedelta(minutes=15))
        verification.set_code(code); verification.save()
        deliver_security_code(user, "Verify your MlimiConnect account", f"Your verification code is {code}. It expires in 15 minutes.", f"MlimiConnect verification code: {code}. It expires in 15 minutes. Never share this code.", "security")
        if account_type != "individual":
            organization = Organization.objects.create(owner=user, **organization_data)
            OrganizationMember.objects.create(organization=organization, user=user, role="owner", status="active", can_procure=True, can_manage_members=True, can_manage_listings=True, can_approve=True, invited_by=user)
        return user

class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["legal_name", "registration_number", "tax_number", "representative_name", "representative_role", "business_size", "member_count", "address", "verification_status"]
        read_only_fields = ["verification_status"]

class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    password = serializers.CharField(write_only=True)
    def validate(self, attrs):
        identifier = attrs["identifier"].strip()
        candidate = User.objects.filter(email__iexact=identifier).first() or User.objects.filter(username__iexact=identifier).first()
        user = authenticate(username=candidate.username if candidate else identifier, password=attrs["password"])
        if not user or not user.is_active:
            raise serializers.ValidationError("Invalid username/email or password.")
        attrs["user"] = user
        return attrs

class WholesalePriceTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = WholesalePriceTier
        fields = ["minimum_quantity", "price_per_unit"]

class ListingSerializer(serializers.ModelSerializer):
    farmer = serializers.SerializerMethodField()
    stock = serializers.IntegerField(source="quantity", read_only=True)
    listingType = serializers.CharField(source="listing_type", read_only=True)
    location = serializers.CharField(source="seller.location", read_only=True)
    rating = serializers.SerializerMethodField()
    reviewsCount = serializers.SerializerMethodField()
    tag = serializers.SerializerMethodField()
    wholesale_tiers = WholesalePriceTierSerializer(many=True, required=False)
    normalized_price = serializers.SerializerMethodField()
    seller_verified = serializers.SerializerMethodField()
    class Meta:
        model = Listing
        fields = ["id", "name", "description", "price", "quantity", "stock", "unit", "pack_size", "minimum_order", "harvest_date", "available_from", "expiry_date", "listing_expires_at", "grade", "variety", "moisture_content", "certification", "is_organic", "storage_conditions", "delivery_radius_km", "latitude", "longitude", "allow_partial_fulfilment", "wholesale_tiers", "normalized_price", "seller_verified", "category", "listing_type", "listingType", "condition", "image", "farmer", "location", "rating", "reviewsCount", "tag", "organization", "shared_with_team", "approval_status", "moderation_reason", "created_at"]
        read_only_fields = ["id", "organization", "shared_with_team", "approval_status", "moderation_reason", "created_at"]
    def get_rating(self, _obj): return 5.0
    def get_reviewsCount(self, _obj): return 0
    def get_tag(self, _obj): return "New"
    def get_normalized_price(self, obj):
        multiplier = 1000 if obj.unit == "tonne" else 1
        return str(obj.price / (obj.pack_size * multiplier)) if obj.pack_size else None
    def get_seller_verified(self, obj): return bool((obj.organization_id and obj.organization.verification_status == "verified") or obj.seller.is_buyer_verified)
    def get_farmer(self, obj):
        if obj.organization_id: return obj.organization.legal_name
        if obj.seller.account_type != "individual" and hasattr(obj.seller, "organization"): return obj.seller.organization.legal_name
        return obj.seller.username
    def validate(self, attrs):
        if attrs.get("pack_size", getattr(self.instance, "pack_size", 1)) <= 0: raise serializers.ValidationError({"pack_size": "Pack size must be greater than zero."})
        if attrs.get("minimum_order", getattr(self.instance, "minimum_order", 1)) < 1: raise serializers.ValidationError({"minimum_order": "Minimum order must be at least one."})
        moisture = attrs.get("moisture_content", getattr(self.instance, "moisture_content", None))
        if moisture is not None and not 0 <= moisture <= 100: raise serializers.ValidationError({"moisture_content": "Moisture content must be between 0 and 100."})
        harvest, expiry = attrs.get("harvest_date", getattr(self.instance, "harvest_date", None)), attrs.get("expiry_date", getattr(self.instance, "expiry_date", None))
        if harvest and expiry and expiry < harvest: raise serializers.ValidationError({"expiry_date": "Expiry cannot be before harvest."})
        tiers = attrs.get("wholesale_tiers", [])
        minimums = [tier["minimum_quantity"] for tier in tiers]
        if len(minimums) != len(set(minimums)) or any(tier["minimum_quantity"] < 1 or tier["price_per_unit"] <= 0 for tier in tiers): raise serializers.ValidationError({"wholesale_tiers": "Tier quantities must be unique and all values must be positive."})
        return attrs
    def _save_tiers(self, listing, tiers):
        if tiers is None: return
        listing.wholesale_tiers.all().delete()
        WholesalePriceTier.objects.bulk_create([WholesalePriceTier(listing=listing, **tier) for tier in tiers])
    def create(self, validated_data):
        tiers = validated_data.pop("wholesale_tiers", [])
        listing = super().create(validated_data); self._save_tiers(listing, tiers); return listing
    def update(self, instance, validated_data):
        tiers = validated_data.pop("wholesale_tiers", None)
        listing = super().update(instance, validated_data); self._save_tiers(listing, tiers); return listing

class MessageSerializer(serializers.ModelSerializer):
    sender_id = serializers.IntegerField(source="sender.id", read_only=True)
    read_at = serializers.SerializerMethodField()
    class Meta:
        model = ChatMessage
        fields = ["id", "sender_id", "text", "created_at", "read_at"]
        read_only_fields = ["id", "sender_id", "created_at", "read_at"]
    def get_read_at(self, obj): return obj.created_at if obj.read_by.exclude(id=obj.sender_id).exists() else None

class ConversationSerializer(serializers.ModelSerializer):
    participant = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    class Meta:
        model = Conversation
        fields = ["id", "participant", "last_message", "unread_count", "updated_at"]
    def other(self, obj): return obj.participants.exclude(id=self.context["request"].user.id).first()
    def get_participant(self, obj):
        other = self.other(obj)
        return {"id": other.id, "username": other.username, "avatar": None, "online": False} if other else {"id": 0, "username": "Unknown"}
    def get_last_message(self, obj):
        message = obj.messages.order_by("-created_at").first()
        return {"text": message.text, "created_at": message.created_at, "sender_id": message.sender_id} if message else None
    def get_unread_count(self, obj): return obj.messages.exclude(sender=self.context["request"].user).exclude(read_by=self.context["request"].user).count()

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "type", "title", "message", "created_at", "read_at", "action_url"]

class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactMessage
        fields = ["name", "email", "subject", "message"]

class NewsletterSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsletterSubscription
        fields = ["email"]

class CheckoutItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)

class CheckoutSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=["airtel_money", "tnm_mpamba", "bank_transfer", "card"])
    items = CheckoutItemSerializer(many=True)
    referral_code = serializers.CharField(required=False, allow_blank=True)
    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("Add at least one item to checkout.")
        product_ids = [item["product_id"] for item in value]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError("Each listing may appear only once; update its quantity instead.")
        return value
    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items")
        now, today = __import__("django.utils.timezone", fromlist=["now"]).now(), __import__("django.utils.timezone", fromlist=["localdate"]).localdate()
        listings = {item.id: item for item in Listing.objects.select_for_update().prefetch_related("wholesale_tiers").filter(id__in=[row["product_id"] for row in items], is_active=True, approval_status="approved").filter(__import__("django.db.models", fromlist=["Q"]).Q(listing_expires_at__isnull=True) | __import__("django.db.models", fromlist=["Q"]).Q(listing_expires_at__gt=now)).filter(__import__("django.db.models", fromlist=["Q"]).Q(available_from__isnull=True) | __import__("django.db.models", fromlist=["Q"]).Q(available_from__lte=today)).filter(__import__("django.db.models", fromlist=["Q"]).Q(expiry_date__isnull=True) | __import__("django.db.models", fromlist=["Q"]).Q(expiry_date__gte=today))}
        if len(listings) != len({row["product_id"] for row in items}):
            raise serializers.ValidationError("One or more listings are unavailable.")
        subtotal = 0
        buyer = self.context["request"].user
        organization = buyer.organization if hasattr(buyer, "organization") else OrganizationMember.objects.filter(user=buyer, status="active", can_procure=True).values_list("organization", flat=True).first()
        order = Order.objects.create(buyer=buyer, organization_id=getattr(organization, "id", organization), payment_method=validated_data["payment_method"])
        for row in items:
            listing = listings[row["product_id"]]
            if row["quantity"] > listing.quantity:
                raise serializers.ValidationError(f"Insufficient stock for {listing.name}.")
            if row["quantity"] < listing.minimum_order: raise serializers.ValidationError(f"Minimum order for {listing.name} is {listing.minimum_order} {listing.unit}.")
            tier = listing.wholesale_tiers.filter(minimum_quantity__lte=row["quantity"]).order_by("-minimum_quantity").first()
            unit_price = tier.price_per_unit if tier else listing.price
            OrderItem.objects.create(order=order, listing=listing, quantity=row["quantity"], unit_price=unit_price)
            listing.quantity -= row["quantity"]
            if listing.quantity == 0:
                listing.is_active = False
            listing.save(update_fields=["quantity", "is_active"])
            subtotal += unit_price * row["quantity"]
        order.subtotal = subtotal
        order.total = subtotal
        order.save(update_fields=["subtotal", "total"])
        OrderStatusHistory.objects.create(order=order, to_status="pending", actor=self.context["request"].user, reason="Order created.")
        return order

class OrderSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()
    status_history = serializers.SerializerMethodField()
    delivery_evidence = serializers.SerializerMethodField()
    refunds = serializers.SerializerMethodField()
    payment_transaction = serializers.SerializerMethodField()
    class Meta:
        model = Order
        fields = ["id", "status", "subtotal", "total", "payment_method", "payment_transaction", "acceptance_deadline", "cancellation_reason", "created_at", "items", "status_history", "delivery_evidence", "refunds"]
    def get_items(self, obj): return [{"listing_id": item.listing_id, "name": item.listing.name, "quantity": item.quantity, "fulfilled_quantity": item.fulfilled_quantity, "unit_price": item.unit_price, "seller": item.listing.seller.username} for item in obj.items.select_related("listing__seller")]
    def get_status_history(self, obj): return list(obj.status_history.values("from_status", "to_status", "reason", "created_at", actor_name=__import__("django.db.models", fromlist=["F"]).F("actor__username")))
    def get_delivery_evidence(self, obj): return list(obj.delivery_evidence.values("id", "evidence_type", "file", "reference", "note", "location", "latitude", "longitude", "signature_name", "created_at"))
    def get_refunds(self, obj): return list(obj.refunds.values("id", "amount", "provider", "provider_reference", "status", "settled_at", "created_at"))
    def get_payment_transaction(self, obj):
        reconciliation = obj.reconciliations.order_by("-created_at").first()
        if not reconciliation:
            return None
        payload = reconciliation.provider_payload if isinstance(reconciliation.provider_payload, dict) else {}
        return {
            "provider": reconciliation.provider,
            "transaction_reference": reconciliation.provider_reference,
            "provider_transaction_id": str(payload.get("provider_transaction_id") or payload.get("provider_reference") or ""),
            "status": reconciliation.status,
            "settled_amount": reconciliation.settled_amount,
            "reconciled_at": reconciliation.reconciled_at,
        }

class TraceabilityEventSerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source="actor.username", read_only=True)
    evidence = serializers.SerializerMethodField()
    class Meta:
        model = TraceabilityEvent
        fields = ["id", "event_type", "stage", "description", "location", "quantity", "unit", "verification_status", "verified_at", "corrects", "previous_hash", "event_hash", "occurred_at", "actor", "evidence"]
    def get_evidence(self, obj):
        if self.context.get("public") and obj.verification_status != "verified": return []
        return [{"id": row.id, "name": row.original_name, "content_type": row.content_type, "size": row.size, "sha256": row.sha256, "url": row.file.url} for row in obj.evidence.all()]

class TraceabilityBatchSerializer(serializers.ModelSerializer):
    events = TraceabilityEventSerializer(many=True, read_only=True)
    integrity = serializers.SerializerMethodField()
    class Meta:
        model = TraceabilityBatch
        fields = ["id", "batch_code", "product", "quantity", "status", "public_data", "created_at", "updated_at", "events", "integrity"]
        read_only_fields = ["id", "created_at", "updated_at", "events"]
    def get_integrity(self, obj):
        from .traceability import verify_chain
        valid, broken_event_id = verify_chain(obj)
        return {"valid": valid, "broken_event_id": broken_event_id, "algorithm": "SHA-256", "event_count": obj.events.count()}

class OrderReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderReview
        fields = ["id", "order", "rating", "comment", "created_at"]
        read_only_fields = ["id", "created_at"]
    def validate_rating(self, value):
        if value < 1 or value > 5: raise serializers.ValidationError("Rating must be between 1 and 5.")
        return value
