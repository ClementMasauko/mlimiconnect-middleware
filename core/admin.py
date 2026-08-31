from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import ContactMessage, CropDiagnosis, DiagnosisEscalation, DiagnosisReport, Dispute, Listing, MessageDelivery, NewsletterSubscription, NotificationPreference, Order, OrderItem, Organization, PasswordResetRequest, USSDCredential, User

admin.site.register(User, UserAdmin)
admin.site.register([Organization, USSDCredential, PasswordResetRequest, Listing, Order, OrderItem, ContactMessage, NewsletterSubscription, NotificationPreference, Dispute, MessageDelivery, CropDiagnosis, DiagnosisReport, DiagnosisEscalation])
