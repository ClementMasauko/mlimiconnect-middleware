from decimal import Decimal
from .storage import protected_media_storage
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
    email_verified = models.BooleanField(default=False)

class EmailVerificationRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_verification_requests")
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    def set_code(self, code): self.code_hash = make_password(code)
    def verify_code(self, code): return not self.used and self.expires_at > __import__("django.utils.timezone", fromlist=["now"]).now() and check_password(code, self.code_hash)

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

class OrganizationMember(models.Model):
    ROLES = [(value, value.replace("_", " ").title()) for value in ["owner", "manager", "procurement", "seller", "member", "auditor"]]
    STATUSES = [("invited", "Invited"), ("active", "Active"), ("suspended", "Suspended")]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="team_members")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="organization_memberships")
    role = models.CharField(max_length=24, choices=ROLES, default="member")
    status = models.CharField(max_length=16, choices=STATUSES, default="invited")
    can_procure = models.BooleanField(default=False)
    can_manage_members = models.BooleanField(default=False)
    can_manage_listings = models.BooleanField(default=False)
    can_approve = models.BooleanField(default=False)
    invited_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="organization_invitations")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: constraints = [models.UniqueConstraint(fields=["organization", "user"], name="unique_organization_member")]

class TeamApprovalRequest(models.Model):
    STATUSES = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="approval_requests")
    action_type = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="team_approval_requests")
    status = models.CharField(max_length=16, choices=STATUSES, default="pending")
    reviewed_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="team_approval_decisions")
    review_reason = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class OrganizationDocument(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="documents")
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT)
    document_type = models.CharField(max_length=50)
    file = models.FileField(upload_to="organization-documents/%Y/%m/", storage=protected_media_storage)
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
    APPROVAL = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("suspended", "Suspended")]
    UNITS = [(value, value.title()) for value in ["kg", "tonne", "bag", "crate", "litre", "item"]]
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name="listings")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True, blank=True, related_name="shared_listings")
    shared_with_team = models.BooleanField(default=False)
    name = models.CharField(max_length=180)
    description = models.TextField()
    price = models.DecimalField(max_digits=14, decimal_places=2)
    quantity = models.PositiveIntegerField()
    unit = models.CharField(max_length=16, choices=UNITS, default="item")
    pack_size = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    minimum_order = models.PositiveIntegerField(default=1)
    harvest_date = models.DateField(null=True, blank=True)
    available_from = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    listing_expires_at = models.DateTimeField(null=True, blank=True)
    grade = models.CharField(max_length=50, blank=True)
    variety = models.CharField(max_length=100, blank=True)
    moisture_content = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    certification = models.CharField(max_length=140, blank=True)
    is_organic = models.BooleanField(default=False)
    storage_conditions = models.TextField(blank=True)
    delivery_radius_km = models.PositiveIntegerField(null=True, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    allow_partial_fulfilment = models.BooleanField(default=False)
    category = models.CharField(max_length=80)
    listing_type = models.CharField(max_length=20, choices=TYPES, default="fixed-price")
    condition = models.CharField(max_length=40, default="new")
    image = models.ImageField(upload_to="listings/", blank=True)
    is_active = models.BooleanField(default=True)
    approval_status = models.CharField(max_length=16, choices=APPROVAL, default="pending")
    moderation_reason = models.TextField(blank=True)
    moderated_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="moderated_listings")
    moderated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class WholesalePriceTier(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="wholesale_tiers")
    minimum_quantity = models.PositiveIntegerField()
    price_per_unit = models.DecimalField(max_digits=14, decimal_places=2)
    class Meta:
        ordering = ["minimum_quantity"]
        constraints = [models.UniqueConstraint(fields=["listing", "minimum_quantity"], name="unique_listing_wholesale_tier")]

class SavedSearch(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_searches")
    name = models.CharField(max_length=100)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class WantedListing(models.Model):
    STATUSES = [("open", "Open"), ("fulfilled", "Fulfilled"), ("closed", "Closed")]
    buyer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="wanted_listings")
    title = models.CharField(max_length=180)
    description = models.TextField()
    category = models.CharField(max_length=80)
    quantity = models.PositiveIntegerField()
    unit = models.CharField(max_length=16, choices=Listing.UNITS, default="item")
    maximum_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    needed_by = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=180, blank=True)
    status = models.CharField(max_length=16, choices=STATUSES, default="open")
    created_at = models.DateTimeField(auto_now_add=True)

class FavouriteListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favourite_listings")
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="favourited_by")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints = [models.UniqueConstraint(fields=["user", "listing"], name="unique_user_favourite_listing")]

class RecentlyViewedListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recently_viewed_listings")
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="viewed_by")
    viewed_at = models.DateTimeField(auto_now=True)
    class Meta: constraints = [models.UniqueConstraint(fields=["user", "listing"], name="unique_user_recent_listing")]

class OperationalEvent(models.Model):
    CATEGORIES = [(value, value.replace("_", " ").title()) for value in ["http", "payment_webhook", "ussd", "uptime", "backup", "alert"]]
    category = models.CharField(max_length=30, choices=CATEGORIES)
    name = models.CharField(max_length=120)
    status = models.CharField(max_length=24)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    correlation_id = models.CharField(max_length=64, db_index=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

class ServiceIncident(models.Model):
    STATUSES = [("investigating", "Investigating"), ("identified", "Identified"), ("monitoring", "Monitoring"), ("resolved", "Resolved")]
    title = models.CharField(max_length=180)
    service = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=STATUSES, default="investigating")
    message = models.TextField()
    started_at = models.DateTimeField(default=__import__("django.utils.timezone", fromlist=["now"]).now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Order(models.Model):
    STATUSES = [(value, value.replace("_", " ").title()) for value in ["pending", "paid", "accepted", "packed", "dispatched", "delivered", "completed", "partially_fulfilled", "failed_delivery", "disputed", "cancelled", "refunded", "fulfilled"]]
    buyer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="orders")
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, null=True, blank=True, related_name="procurement_orders")
    status = models.CharField(max_length=20, choices=STATUSES, default="pending")
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    payment_method = models.CharField(max_length=30)
    provider_reference = models.CharField(max_length=120, blank=True, unique=True, null=True)
    acceptance_deadline = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    listing = models.ForeignKey(Listing, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    fulfilled_quantity = models.PositiveIntegerField(default=0)
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

class MessageDelivery(models.Model):
    CHANNELS = [("email", "Email"), ("sms", "SMS")]
    STATUSES = [(value, value.title()) for value in ["pending", "accepted", "delivered", "failed", "skipped"]]
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="message_deliveries")
    channel = models.CharField(max_length=10, choices=CHANNELS)
    category = models.CharField(max_length=40)
    provider = models.CharField(max_length=40)
    recipient_hint = models.CharField(max_length=40)
    status = models.CharField(max_length=16, choices=STATUSES, default="pending")
    provider_reference = models.CharField(max_length=120, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=1)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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

class CropDiagnosis(models.Model):
    STATUSES = [(value, value.title()) for value in ["completed", "provider_error", "deletion_pending", "deleted"]]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="crop_diagnoses")
    provider = models.CharField(max_length=40, default="kindwise_crop_health")
    provider_reference = models.CharField(max_length=160, blank=True)
    crop = models.CharField(max_length=80, blank=True)
    image_sha256 = models.CharField(max_length=64)
    original_filename = models.CharField(max_length=180, blank=True)
    consent_version = models.CharField(max_length=20)
    consented_at = models.DateTimeField()
    results = models.JSONField(default=dict)
    status = models.CharField(max_length=24, choices=STATUSES, default="completed")
    remote_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class DiagnosisReport(models.Model):
    CATEGORIES = [(value, value.replace("_", " ").title()) for value in ["harmful_advice", "incorrect_result", "unsafe_pesticide", "privacy", "other"]]
    diagnosis = models.ForeignKey(CropDiagnosis, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(User, on_delete=models.PROTECT, related_name="diagnosis_reports")
    category = models.CharField(max_length=30, choices=CATEGORIES)
    details = models.TextField()
    status = models.CharField(max_length=20, default="open")
    created_at = models.DateTimeField(auto_now_add=True)

class DiagnosisEscalation(models.Model):
    diagnosis = models.ForeignKey(CropDiagnosis, on_delete=models.CASCADE, related_name="escalations")
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="diagnosis_escalations")
    reason = models.TextField()
    status = models.CharField(max_length=20, default="requested")
    created_at = models.DateTimeField(auto_now_add=True)

class HistoricalMarketPrice(models.Model):
    """A versioned World Bank monthly market-price estimate, never a live quote."""
    CROP_CHOICES = [(value, value.title()) for value in ["beans", "cassava", "groundnuts", "maize", "rice"]]
    source = models.CharField(max_length=120, default="World Bank Microdata Library")
    source_dataset = models.CharField(max_length=80, default="MWI_2021_RTFP_v02_M")
    source_version = models.DateField()
    source_url = models.URLField(default="https://microdata.worldbank.org/catalog/6171")
    region = models.CharField(max_length=100)
    district = models.CharField(max_length=100)
    market = models.CharField(max_length=120)
    geo_id = models.CharField(max_length=80)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    price_date = models.DateField(db_index=True)
    crop = models.CharField(max_length=20, choices=CROP_CHOICES, db_index=True)
    currency = models.CharField(max_length=3, default="MWK")
    unit = models.CharField(max_length=16, default="kg")
    opening_price = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    high_price = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    low_price = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    closing_price = models.DecimalField(max_digits=14, decimal_places=4)
    trust_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    data_coverage = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    recent_data_coverage = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    index_confidence_score = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    spatially_interpolated = models.BooleanField(default=False)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-price_date", "market", "crop"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_version", "geo_id", "price_date", "crop"],
                name="unique_historical_market_price",
            )
        ]
        indexes = [
            models.Index(fields=["crop", "price_date"], name="market_crop_date_idx"),
            models.Index(fields=["district", "market"], name="market_location_idx"),
        ]

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
    VERIFICATION = [("pending", "Pending"), ("verified", "Verified"), ("rejected", "Rejected")]
    batch = models.ForeignKey(TraceabilityBatch, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    stage = models.CharField(max_length=80)
    event_type = models.CharField(max_length=80)
    description = models.TextField()
    location = models.CharField(max_length=140, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit = models.CharField(max_length=24)
    verification_status = models.CharField(max_length=16, choices=VERIFICATION, default="pending")
    verified_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="verified_traceability_events")
    verified_at = models.DateTimeField(null=True, blank=True)
    corrects = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="corrections")
    previous_hash = models.CharField(max_length=64, blank=True)
    event_hash = models.CharField(max_length=64, unique=True)
    occurred_at = models.DateTimeField(default=__import__("django.utils.timezone", fromlist=["now"]).now)

class TraceabilityEvidence(models.Model):
    event = models.ForeignKey(TraceabilityEvent, on_delete=models.CASCADE, related_name="evidence")
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT)
    file = models.FileField(upload_to="traceability-evidence/", storage=protected_media_storage)
    original_name = models.CharField(max_length=180)
    content_type = models.CharField(max_length=80)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

class TraceabilityAudit(models.Model):
    batch = models.ForeignKey(TraceabilityBatch, on_delete=models.CASCADE, related_name="audit_history")
    event = models.ForeignKey(TraceabilityEvent, on_delete=models.PROTECT, null=True, blank=True)
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    action = models.CharField(max_length=80)
    reason = models.TextField(blank=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

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
    evidence = models.JSONField(default=list)
    decision = models.CharField(max_length=20, blank=True)
    resolution_note = models.TextField(blank=True)
    refund_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="decided_disputes")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AuditLog(models.Model):
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="audit_actions")
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=80)
    target_id = models.CharField(max_length=80)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class TransporterProfile(models.Model):
    STATUSES = [("pending", "Pending"), ("verified", "Verified"), ("rejected", "Rejected"), ("suspended", "Suspended")]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="transporter_profile")
    vehicle_type = models.CharField(max_length=120)
    capacity_kg = models.PositiveIntegerField()
    license_reference = models.CharField(max_length=120)
    verification_status = models.CharField(max_length=20, choices=STATUSES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class TransporterDocument(models.Model):
    profile = models.ForeignKey(TransporterProfile, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=40)
    file = models.FileField(upload_to="transporter-documents/%Y/%m/", storage=protected_media_storage)
    verification_status = models.CharField(max_length=16, choices=[("pending", "Pending"), ("verified", "Verified"), ("rejected", "Rejected")], default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

class Delivery(models.Model):
    STATUSES = [("open_for_quotes", "Open for quotes"), ("unassigned", "Unassigned"), ("assigned", "Assigned"), ("picked_up", "Picked up"), ("delivered", "Delivered"), ("failed_delivery", "Failed delivery"), ("cancelled", "Cancelled")]
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="delivery")
    transporter = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="deliveries")
    pickup_location = models.CharField(max_length=180)
    delivery_location = models.CharField(max_length=180)
    pickup_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    pickup_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    pickup_osm_reference = models.CharField(max_length=80, blank=True)
    delivery_osm_reference = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default="unassigned")
    distance_km = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0"))
    delivery_fee = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    liability_rule = models.TextField(blank=True)
    liability_accepted_at = models.DateTimeField(null=True, blank=True)
    external_provider = models.CharField(max_length=60, blank=True)
    external_reference = models.CharField(max_length=120, blank=True)
    failure_reason = models.TextField(blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class GeocodingCache(models.Model):
    query_hash = models.CharField(max_length=64, unique=True)
    normalized_query = models.CharField(max_length=180)
    results = models.JSONField(default=list)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class GeocodingRequestState(models.Model):
    key = models.CharField(max_length=20, unique=True, default="nominatim")
    last_requested_at = models.DateTimeField(null=True, blank=True)

class DeliveryQuote(models.Model):
    STATUSES = [("pending", "Pending"), ("accepted", "Accepted"), ("rejected", "Rejected"), ("withdrawn", "Withdrawn")]
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="quotes")
    transporter = models.ForeignKey(User, on_delete=models.PROTECT, related_name="delivery_quotes")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    estimated_hours = models.PositiveIntegerField()
    note = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUSES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints = [models.UniqueConstraint(fields=["delivery", "transporter"], name="unique_delivery_quote")]

class DeliveryLocationUpdate(models.Model):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="location_updates")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    accuracy_m = models.PositiveIntegerField(null=True, blank=True)
    status_note = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class DeliveryRating(models.Model):
    delivery = models.OneToOneField(Delivery, on_delete=models.CASCADE, related_name="rating")
    buyer = models.ForeignKey(User, on_delete=models.PROTECT)
    score = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class PaymentReconciliation(models.Model):
    STATUSES = [("pending", "Pending"), ("matched", "Matched"), ("mismatch", "Mismatch"), ("refunded", "Refunded")]
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="reconciliations")
    provider = models.CharField(max_length=40)
    provider_reference = models.CharField(max_length=120, unique=True)
    expected_amount = models.DecimalField(max_digits=14, decimal_places=2)
    settled_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default="pending")
    provider_payload = models.JSONField(default=dict)
    reconciled_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="reconciled_payments")
    reconciled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class DeliveryEvidence(models.Model):
    TYPES = [("pickup", "Pickup"), ("loading", "Loading"), ("dispatch", "Dispatch"), ("unloading", "Unloading"), ("delivery", "Delivery"), ("failed_delivery", "Failed delivery")]
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="delivery_evidence")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    evidence_type = models.CharField(max_length=24, choices=TYPES)
    file = models.FileField(upload_to="delivery-evidence/", blank=True, storage=protected_media_storage)
    reference = models.CharField(max_length=240, blank=True)
    note = models.TextField(blank=True)
    location = models.CharField(max_length=180, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    signature_name = models.CharField(max_length=140, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Refund(models.Model):
    STATUSES = [("requested", "Requested"), ("submitted", "Submitted"), ("settled", "Settled"), ("failed", "Failed")]
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    provider = models.CharField(max_length=40)
    provider_reference = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=20, choices=STATUSES, default="requested")
    reason = models.TextField()
    provider_payload = models.JSONField(default=dict)
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="requested_refunds")
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Livestock records deliberately separate husbandry records from marketplace
# listings. A farmer can therefore manage a herd without publishing animals for
# sale, and live-animal sales can carry their own verification documents.
class LivestockProfile(models.Model):
    SYSTEMS = [(value, value.replace("_", " ").title()) for value in ["pastoral", "mixed", "intensive", "free_range", "backyard"]]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="livestock_profile")
    farm_name = models.CharField(max_length=160)
    production_system = models.CharField(max_length=24, choices=SYSTEMS, default="mixed")
    species_kept = models.JSONField(default=list)
    district = models.CharField(max_length=100, blank=True)
    traditional_authority = models.CharField(max_length=100, blank=True)
    biosecurity_notes = models.TextField(blank=True)
    veterinary_contact = models.CharField(max_length=140, blank=True)
    verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class HerdFlock(models.Model):
    SPECIES = [(value, value.replace("_", " ").title()) for value in ["cattle", "goats", "sheep", "pigs", "chickens_broilers", "chickens_layers", "chickens_indigenous", "ducks", "rabbits", "other"]]
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="herds_flocks")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True, blank=True, related_name="herds_flocks")
    name = models.CharField(max_length=140)
    species = models.CharField(max_length=32, choices=SPECIES)
    breed = models.CharField(max_length=100, blank=True)
    purpose = models.CharField(max_length=100, blank=True)
    head_count = models.PositiveIntegerField(default=0)
    housing = models.CharField(max_length=180, blank=True)
    traceability_batch = models.OneToOneField(TraceabilityBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name="livestock_herd")
    status = models.CharField(max_length=16, choices=[("active", "Active"), ("sold", "Sold"), ("closed", "Closed")], default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class AnimalRecord(models.Model):
    herd = models.ForeignKey(HerdFlock, on_delete=models.CASCADE, related_name="animals")
    identifier = models.CharField(max_length=100)
    sex = models.CharField(max_length=12, choices=[("female", "Female"), ("male", "Male"), ("unknown", "Unknown")], default="unknown")
    date_of_birth = models.DateField(null=True, blank=True)
    estimated_age_months = models.PositiveIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    acquisition_type = models.CharField(max_length=16, choices=[("birth", "Birth"), ("purchase", "Purchase"), ("transfer", "Transfer"), ("unknown", "Unknown")], default="unknown")
    acquisition_date = models.DateField(null=True, blank=True)
    breeding_status = models.CharField(max_length=24, choices=[("not_recorded", "Not recorded"), ("not_breeding", "Not breeding"), ("breeding", "Breeding"), ("pregnant", "Pregnant"), ("lactating", "Lactating")], default="not_recorded")
    status = models.CharField(max_length=20, choices=[("active", "Active"), ("sold", "Sold"), ("deceased", "Deceased"), ("missing", "Missing")], default="active")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["herd", "identifier"], name="unique_animal_identifier_per_herd")]

class LivestockHealthEvent(models.Model):
    TYPES = [(value, value.replace("_", " ").title()) for value in ["vaccination", "treatment", "illness", "injury", "inspection", "quarantine", "birth", "death"]]
    VERIFICATION = [("pending", "Pending"), ("verified", "Verified"), ("rejected", "Rejected")]
    herd = models.ForeignKey(HerdFlock, on_delete=models.CASCADE, related_name="health_events")
    animal = models.ForeignKey(AnimalRecord, on_delete=models.SET_NULL, null=True, blank=True, related_name="health_events")
    event_type = models.CharField(max_length=24, choices=TYPES)
    occurred_on = models.DateField()
    description = models.TextField()
    product_or_vaccine = models.CharField(max_length=140, blank=True)
    administered_by = models.CharField(max_length=140, blank=True)
    withdrawal_end_date = models.DateField(null=True, blank=True)
    evidence = models.FileField(upload_to="livestock-health/%Y/%m/", blank=True, storage=protected_media_storage)
    verification_status = models.CharField(max_length=16, choices=VERIFICATION, default="pending")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_livestock_health_events")
    created_at = models.DateTimeField(auto_now_add=True)

class LivestockProductionRecord(models.Model):
    TYPES = [(value, value.replace("_", " ").title()) for value in ["eggs", "milk", "manure", "live_weight", "feed_used", "mortality"]]
    herd = models.ForeignKey(HerdFlock, on_delete=models.CASCADE, related_name="production_records")
    record_type = models.CharField(max_length=20, choices=TYPES)
    recorded_on = models.DateField()
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=20)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class VaccinationReminder(models.Model):
    STATUSES = [("scheduled", "Scheduled"), ("completed", "Completed"), ("cancelled", "Cancelled")]
    herd = models.ForeignKey(HerdFlock, on_delete=models.CASCADE, related_name="vaccination_reminders")
    animal = models.ForeignKey(AnimalRecord, on_delete=models.SET_NULL, null=True, blank=True)
    vaccine_name = models.CharField(max_length=140)
    due_on = models.DateField()
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUSES, default="scheduled")
    notification_sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class LiveAnimalListingDetail(models.Model):
    STATUSES = [("pending", "Pending"), ("verified", "Verified"), ("rejected", "Rejected")]
    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="live_animal_detail")
    herd = models.ForeignKey(HerdFlock, on_delete=models.PROTECT, related_name="live_listings")
    species = models.CharField(max_length=32, choices=HerdFlock.SPECIES)
    breed = models.CharField(max_length=100, blank=True)
    sex = models.CharField(max_length=12, choices=[("female", "Female"), ("male", "Male"), ("mixed", "Mixed")], default="mixed")
    age_months = models.PositiveIntegerField(null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    sale_format = models.CharField(max_length=16, choices=[("individual", "Individual"), ("group", "Group")], default="group")
    sale_quantity = models.PositiveIntegerField(default=1)
    live_weight_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    purpose = models.CharField(max_length=16, choices=[("breeding", "Breeding"), ("meat", "Meat"), ("milk", "Milk"), ("eggs", "Eggs"), ("draught", "Draught"), ("other", "Other")], default="other")
    health_inspection_date = models.DateField(null=True, blank=True)
    animal_identifier = models.CharField(max_length=120, blank=True)
    breeding_status = models.CharField(max_length=100, blank=True)
    production_summary = models.TextField(blank=True)
    transport_available = models.BooleanField(default=False)
    handling_requirements = models.TextField(blank=True)
    vaccination_summary = models.TextField(blank=True)
    welfare_declaration = models.BooleanField(default=False)
    movement_permit_reference = models.CharField(max_length=120, blank=True)
    veterinary_certificate = models.FileField(upload_to="live-animal-documents/%Y/%m/", blank=True, storage=protected_media_storage)
    verification_status = models.CharField(max_length=16, choices=STATUSES, default="pending")
    verified_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="verified_live_animal_listings")
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class LivestockDeliveryRequirement(models.Model):
    delivery = models.OneToOneField(Delivery, on_delete=models.CASCADE, related_name="livestock_requirements")
    movement_permit_reference = models.CharField(max_length=120, blank=True)
    transporter_authorization_reference = models.CharField(max_length=120, blank=True)
    vehicle_suitable = models.BooleanField(default=False)
    ventilation_confirmed = models.BooleanField(default=False)
    cleaning_confirmed = models.BooleanField(default=False)
    disinfection_record = models.TextField(blank=True)
    maximum_stocking_count = models.PositiveIntegerField(null=True, blank=True)
    stocking_plan = models.TextField(blank=True)
    welfare_plan = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=120, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

class AnimalWeightRecord(models.Model):
    animal = models.ForeignKey(AnimalRecord, on_delete=models.CASCADE, related_name="weight_history")
    recorded_on = models.DateField()
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class LivestockBreedingRecord(models.Model):
    TYPES = [(value, value.replace("_", " ").title()) for value in ["mating", "insemination", "pregnancy_check", "birth", "weaning"]]
    herd = models.ForeignKey(HerdFlock, on_delete=models.CASCADE, related_name="breeding_records")
    animal = models.ForeignKey(AnimalRecord, on_delete=models.SET_NULL, null=True, blank=True, related_name="breeding_records")
    event_type = models.CharField(max_length=24, choices=TYPES)
    occurred_on = models.DateField()
    expected_due_date = models.DateField(null=True, blank=True)
    outcome = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class LivestockFinancialRecord(models.Model):
    TYPES = [("expense", "Expense"), ("income", "Income")]
    herd = models.ForeignKey(HerdFlock, on_delete=models.CASCADE, related_name="financial_records")
    record_type = models.CharField(max_length=12, choices=TYPES)
    category = models.CharField(max_length=60)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    occurred_on = models.DateField()
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class LivestockAlert(models.Model):
    LEVELS = [("info", "Information"), ("warning", "Warning"), ("critical", "Critical")]
    herd = models.ForeignKey(HerdFlock, on_delete=models.CASCADE, related_name="alerts")
    alert_type = models.CharField(max_length=40)
    level = models.CharField(max_length=12, choices=LEVELS, default="warning")
    message = models.TextField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class LivestockMovementRestriction(models.Model):
    species = models.CharField(max_length=32, blank=True)
    origin_region = models.CharField(max_length=100, blank=True)
    destination_region = models.CharField(max_length=100, blank=True)
    reason = models.TextField()
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    source_name = models.CharField(max_length=180)
    source_reference = models.URLField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class AnimalWelfareReport(models.Model):
    STATUSES = [("open", "Open"), ("investigating", "Investigating"), ("resolved", "Resolved"), ("dismissed", "Dismissed")]
    reporter = models.ForeignKey(User, on_delete=models.PROTECT, related_name="animal_welfare_reports")
    listing = models.ForeignKey(Listing, on_delete=models.SET_NULL, null=True, blank=True, related_name="welfare_reports")
    delivery = models.ForeignKey(Delivery, on_delete=models.SET_NULL, null=True, blank=True, related_name="welfare_reports")
    category = models.CharField(max_length=60)
    details = models.TextField()
    evidence = models.FileField(upload_to="animal-welfare-reports/%Y/%m/", blank=True, storage=protected_media_storage)
    status = models.CharField(max_length=16, choices=STATUSES, default="open")
    resolution = models.TextField(blank=True)
    assigned_to = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="assigned_welfare_reports")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class LivestockCatalogueEntry(models.Model):
    TYPES = [("species", "Species"), ("breed", "Breed"), ("prohibited_sale", "Prohibited sale rule")]
    entry_type = models.CharField(max_length=24, choices=TYPES)
    name = models.CharField(max_length=120)
    species = models.CharField(max_length=32, blank=True)
    active = models.BooleanField(default=True)
    rules = models.JSONField(default=dict)
    updated_by = models.ForeignKey(User, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)
