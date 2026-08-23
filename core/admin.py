from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import ContactMessage, Dispute, Listing, NewsletterSubscription, NotificationPreference, Order, OrderItem, Organization, PasswordResetRequest, USSDCredential, User

admin.site.register(User, UserAdmin)
admin.site.register([Organization, USSDCredential, PasswordResetRequest, Listing, Order, OrderItem, ContactMessage, NewsletterSubscription, NotificationPreference, Dispute])
