from django.contrib.auth import authenticate, password_validation
from django.db import transaction
from rest_framework import serializers
from .models import ChatMessage, ContactMessage, Conversation, Listing, NewsletterSubscription, Notification, Order, OrderItem, OrderReview, Organization, Subscription, TraceabilityBatch, TraceabilityEvent, User

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
        fields = ["id", "username", "email", "phone", "location", "user_type", "account_type", "can_buy", "can_sell", "organization_status", "isBuyerVerified", "subscription"]
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
        if account_type != "individual": Organization.objects.create(owner=user, **organization_data)
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

class ListingSerializer(serializers.ModelSerializer):
    farmer = serializers.SerializerMethodField()
    stock = serializers.IntegerField(source="quantity", read_only=True)
    listingType = serializers.CharField(source="listing_type", read_only=True)
    location = serializers.CharField(source="seller.location", read_only=True)
    rating = serializers.SerializerMethodField()
    reviewsCount = serializers.SerializerMethodField()
    tag = serializers.SerializerMethodField()
    class Meta:
        model = Listing
        fields = ["id", "name", "description", "price", "quantity", "stock", "category", "listing_type", "listingType", "condition", "image", "farmer", "location", "rating", "reviewsCount", "tag", "created_at"]
        read_only_fields = ["id", "created_at"]
    def get_rating(self, _obj): return 5.0
    def get_reviewsCount(self, _obj): return 0
    def get_tag(self, _obj): return "New"
    def get_farmer(self, obj):
        if obj.seller.account_type != "individual" and hasattr(obj.seller, "organization"): return obj.seller.organization.legal_name
        return obj.seller.username

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
    payment_method = serializers.ChoiceField(choices=["airtel_money", "tnm_mpamba", "bank_transfer"])
    items = CheckoutItemSerializer(many=True)
    referral_code = serializers.CharField(required=False, allow_blank=True)
    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items")
        listings = {item.id: item for item in Listing.objects.select_for_update().filter(id__in=[row["product_id"] for row in items], is_active=True)}
        if len(listings) != len({row["product_id"] for row in items}):
            raise serializers.ValidationError("One or more listings are unavailable.")
        subtotal = 0
        order = Order.objects.create(buyer=self.context["request"].user, payment_method=validated_data["payment_method"])
        for row in items:
            listing = listings[row["product_id"]]
            if row["quantity"] > listing.quantity:
                raise serializers.ValidationError(f"Insufficient stock for {listing.name}.")
            OrderItem.objects.create(order=order, listing=listing, quantity=row["quantity"], unit_price=listing.price)
            subtotal += listing.price * row["quantity"]
        order.subtotal = subtotal
        order.total = subtotal
        order.save(update_fields=["subtotal", "total"])
        return order

class OrderSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()
    class Meta:
        model = Order
        fields = ["id", "status", "subtotal", "total", "payment_method", "created_at", "items"]
    def get_items(self, obj): return [{"listing_id": item.listing_id, "name": item.listing.name, "quantity": item.quantity, "unit_price": item.unit_price, "seller": item.listing.seller.username} for item in obj.items.select_related("listing__seller")]

class TraceabilityEventSerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source="actor.username", read_only=True)
    class Meta:
        model = TraceabilityEvent
        fields = ["id", "stage", "description", "location", "occurred_at", "actor"]

class TraceabilityBatchSerializer(serializers.ModelSerializer):
    events = TraceabilityEventSerializer(many=True, read_only=True)
    class Meta:
        model = TraceabilityBatch
        fields = ["id", "batch_code", "product", "quantity", "status", "public_data", "created_at", "updated_at", "events"]
        read_only_fields = ["id", "created_at", "updated_at", "events"]

class OrderReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderReview
        fields = ["id", "order", "rating", "comment", "created_at"]
        read_only_fields = ["id", "created_at"]
    def validate_rating(self, value):
        if value < 1 or value > 5: raise serializers.ValidationError("Rating must be between 1 and 5.")
        return value
