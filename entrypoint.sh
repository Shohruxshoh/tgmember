#!/bin/bash

echo "⏳ Database tayyormi, tekshirilmoqda..."
# DB tayyor bo'lguncha kutish (psql o‘rniga socket testi)
python - <<'PYCODE'
import os, time, socket
host = os.getenv("DB_HOST", "db")
port = int(os.getenv("DB_PORT", "5432"))
for i in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            print("✅ DB available")
            break
    except Exception:
        print("... kutyapman (DB hali tayyor emas)")
        time.sleep(2)
else:
    raise SystemExit("❌ DB ulanmayapti")
PYCODE

echo "🔧 Migrate ishlayapti..."
python manage.py migrate --noinput

echo "🗂️  Collectstatic ishlayapti..."
python manage.py collectstatic --noinput

if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  echo "👤 Superuser tekshirilmoqda..."
  python manage.py shell <<'EOF'
import os
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.getenv("DJANGO_SUPERUSER_USERNAME")
email = os.getenv("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
password = os.getenv("DJANGO_SUPERUSER_PASSWORD")
if username and password and not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username=username, email=email, password=password)
    print("✅ Superuser yaratildi.")
else:
    print("ℹ️  Superuser mavjud yoki env to‘liq emas.")
EOF
fi

echo "🚀 App ishga tushmoqda..."

exec "$@"
