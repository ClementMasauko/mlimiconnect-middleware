# MlimiConnect backend

Django REST backend for the MlimiConnect frontend. It uses secure cookie sessions, CSRF protection, persistent marketplace listings, server-calculated checkout totals, contact/newsletter storage, notification preferences, disputes, and account deactivation.

## Local setup

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
py manage.py makemigrations core
py manage.py migrate
py manage.py createsuperuser
py manage.py runserver
```

Then configure the frontend with `VITE_API_URL=http://localhost:8000`. Requests use credentials and obtain a CSRF token automatically. The default allowed frontend origin is `http://localhost:5173`.

## Included API surface

- Cookie authentication, registration, logout, and profiles
- Public and seller-owned marketplace listings
- Transactional order creation with server-owned prices and stock checks
- Contact messages and newsletter subscriptions
- Notification preferences, disputes, referrals, and account deactivation
- Django admin for operational review

## USSD authentication

Set the same random `USSD_SERVICE_KEY` in this backend and the USSD service. User phone numbers must use `+265...` format. Create or rotate a PIN without storing it in plaintext:

```powershell
py manage.py set_ussd_pin username 1234
```

The service-to-service endpoint is `POST /api/ussd/authenticate` and requires the secret in `X-USSD-Service-Key`.

Keep `PAYMENTS_ENABLED=false` until a licensed provider adapter and signed, idempotent webhook are configured. SQLite is suitable for development; configure PostgreSQL for production.
