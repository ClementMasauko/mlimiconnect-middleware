from django.urls import path
from . import views

urlpatterns = [
    path("csrf/", views.CsrfView.as_view()),
    path("auth/register/", views.RegisterView.as_view()),
    path("auth/verify-otp/", views.ProfileView.as_view()),
    path("auth/login/", views.LoginView.as_view()),
    path("auth/logout/", views.LogoutView.as_view()),
    path("auth/profile/", views.ProfileView.as_view()),
    path("auth/forgot-password/", views.ForgotPasswordView.as_view()),
    path("auth/verify-reset-otp/", views.VerifyResetCodeView.as_view()),
    path("auth/reset-password/", views.ResetPasswordView.as_view()),
    path("organizations/me/", views.OrganizationProfileView.as_view()),
    path("ussd/authenticate", views.USSDAuthenticateView.as_view()),
    path("marketplace/public-listings/", views.PublicListingList.as_view()),
    path("marketplace/listings/", views.ListingListCreate.as_view()),
    path("marketplace/orders/", views.OrderList.as_view()),
    path("marketplace/orders/<int:order_id>/dispute/", views.DisputeCreate.as_view()),
    path("payments/checkout-sessions/", views.CheckoutView.as_view()),
    path("referrals/validate/", views.ReferralValidate.as_view()),
    path("contact/", views.ContactCreate.as_view()),
    path("newsletter/", views.NewsletterCreate.as_view()),
    path("users/notifications", views.NotificationPreferencesView.as_view()),
    path("users/account", views.DeleteAccountView.as_view()),
]
