from decimal import Decimal
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
import uuid

class User(AbstractUser):
    USER_TYPES = [("farmer", "Farmer"), ("buyer", "Buyer"), ("organization", "Organization"), ("admin", "Admin")]
    ACCOUNT_TYPES = [("individual", "Individual"), ("cooperative", "Cooperative"), ("company", "Company"), ("ngo", "NGO"), ("government", "Government"), ("institution", "Institution")]
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=24, blank=True)
    location = models.CharField(max_length=120, blank=True)
    user_type = models.CharField(max_length=16, choices=USER_TYPES, default="buyer")
    account_type = models.CharField(max_length=16, choices=ACCOUNT_TYPES, default="individual")
    can_buy = models.BooleanField(default=True)
    can_sell = models.BooleanField(default=False)
    is_buyer_verified = models.BooleanField(default=False)

class Organization(models.Model):
    SIZES = [("small", "Small"), ("medium", "Medium"), ("large", "Large")]
    VERIFICATION = [("pending", "Pending"), ("verified", "Verified"), ("rejected", "Rejected")]
    owner = models.OneToOneField(User, on_delete=models.CASCADE, related_name="organization")
    legal_name = models.CharField(max_length=180)
    registration_number = models.CharField(max_length=80, unique=True)
    tax_number = models.CharField(max_length=80, blank=True)
    representative_name = models.CharField(max_length=140)
    representative_role = models.CharField(max_length=100)
    business_size = models.CharField(max_length=12, choices=SIZES, default="small")
    member_count = models.PositiveIntegerField(null=True, blank=True)
    address = models.TextField()
    verification_status = models.CharField(max_length=16, choices=VERIFICATION, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

class USSDCredential(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="ussd_credential")
    pin_hash = models.CharField(max_length=128)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    def set_pin(self, pin):
        if not isinstance(pin, str) or not pin.isdigit() or len(pin) != 4: raise ValueError("USSD PIN must contain exactly four digits.")
        self.pin_hash = make_password(pin)
    def verify(self, pin): return self.enabled and check_password(pin, self.pin_hash)

class PasswordResetRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_requests")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    verified = models.BooleanField(default=False)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    def set_code(self, code): self.code_hash = make_password(code)
    def verify_code(self, code): return check_password(code, self.code_hash)

class AccountDeletionRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="account_deletion_requests")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    def set_code(self, code): self.code_hash = make_password(code)
    def verify_code(self, code): return check_password(code, self.code_hash)

class Listing(models.Model):
    TYPES = [("fixed-price", "Fixed price"), ("auction", "Auction"), ("both", "Both")]
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name="listings")
    name = models.CharField(max_length=180)
    description = models.TextField()
    price = models.DecimalField(max_digits=14, decimal_places=2)
    quantity = models.PositiveIntegerField()
    category = models.CharField(max_length=80)
    listing_type = models.CharField(max_length=20, choices=TYPES, default="fixed-price")
    condition = models.CharField(max_length=40, default="new")
    image = models.ImageField(upload_to="listings/", blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Order(models.Model):
    STATUSES = [("pending", "Pending"), ("paid", "Paid"), ("cancelled", "Cancelled"), ("fulfilled", "Fulfilled")]
    buyer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=STATUSES, default="pending")
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    payment_method = models.CharField(max_length=30)
    provider_reference = models.CharField(max_length=120, blank=True, unique=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    listing = models.ForeignKey(Listing, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)

class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    subject = models.CharField(max_length=180)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

class NewsletterSubscription(models.Model):
    email = models.EmailField(unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class NotificationPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="notification_preferences")
    settings = models.JSONField(default=dict)

class Subscription(models.Model):
    PLANS = [(value, value.replace("-", " ").title()) for value in ["free", "farmer-plus", "buyer-pro", "cooperative", "organization", "enterprise"]]
    STATUSES = [(value, value.replace("_", " ").title()) for value in ["pending_payment", "active", "cancelled", "past_due"]]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="subscription")
    plan_id = models.CharField(max_length=32, choices=PLANS, default="free")
    status = models.CharField(max_length=24, choices=STATUSES, default="active")
    billing_cycle = models.CharField(max_length=12, choices=[("monthly", "Monthly"), ("annual", "Annual")], default="monthly")
    renews_at = models.DateTimeField(null=True, blank=True)
    enabled_features = models.JSONField(default=list)
    provider_reference = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class Conversation(models.Model):
    participants = models.ManyToManyField(User, related_name="conversations")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class ChatMessage(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_messages")
    text = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    read_by = models.ManyToManyField(User, blank=True, related_name="read_messages")

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    message = models.TextField()
    action_url = models.CharField(max_length=300, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AdvisoryUsage(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="advisory_usage")
    period = models.CharField(max_length=7)
    ai_requests = models.PositiveIntegerField(default=0)
    expert_credits_used = models.PositiveIntegerField(default=0)

class ExpertConsultation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="expert_consultations")
    expert_id = models.PositiveIntegerField()
    starts_at = models.DateTimeField()
    status = models.CharField(max_length=20, default="requested")
    created_at = models.DateTimeField(auto_now_add=True)

class OrderReview(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="review")
    reviewer = models.ForeignKey(User, on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class WalletTransaction(models.Model):
    TYPES = [(value, value.title()) for value in ["sale", "purchase", "withdrawal", "refund", "adjustment"]]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="wallet_transactions")
    type = models.CharField(max_length=20, choices=TYPES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, default="pending")
    reference = models.CharField(max_length=120, unique=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class TraceabilityBatch(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="traceability_batches")
    batch_code = models.CharField(max_length=80, unique=True)
    product = models.CharField(max_length=180)
    quantity = models.CharField(max_length=80)
    status = models.CharField(max_length=40, default="created")
    public_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class TraceabilityEvent(models.Model):
    batch = models.ForeignKey(TraceabilityBatch, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    stage = models.CharField(max_length=80)
    description = models.TextField()
    location = models.CharField(max_length=140, blank=True)
    occurred_at = models.DateTimeField(default=__import__("django.utils.timezone", fromlist=["now"]).now)

class SmartContract(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="smart_contracts")
    name = models.CharField(max_length=180)
    terms = models.JSONField(default=dict)
    status = models.CharField(max_length=30, default="draft")
    created_at = models.DateTimeField(auto_now_add=True)

class PlatformSetting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

class Dispute(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="disputes")
    opened_by = models.ForeignKey(User, on_delete=models.PROTECT)
    reason = models.TextField()
    status = models.CharField(max_length=20, default="open")
    created_at = models.DateTimeField(auto_now_add=True)
