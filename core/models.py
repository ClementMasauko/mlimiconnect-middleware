from decimal import Decimal
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
import uuid

class User(AbstractUser):
    USER_TYPES = [("farmer", "Farmer"), ("buyer", "Buyer"), ("organization", "Organization"), ("admin", "Admin")]
    ACCOUNT_TYPES = [("individual", "Individual"), ("cooperative", "Cooperative"), ("company", "Company")]
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

class Dispute(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="disputes")
    opened_by = models.ForeignKey(User, on_delete=models.PROTECT)
    reason = models.TextField()
    status = models.CharField(max_length=20, default="open")
    created_at = models.DateTimeField(auto_now_add=True)
