from datetime import timedelta
from django.db import transaction
from django.db.models import F, Sum, Q, Case, When, Value
from django.utils.timezone import now
from celery import shared_task
from order.enums import PaidEnum
from order.models import OrderMember
from users.models import User
from asgiref.sync import async_to_sync
from service.models import Service
from .telegram_fetch import fetch_prior_message_urls
from .services import save_links_for_order


# @shared_task
# def process_telegram_checklist(data, user_id):
#     """
#     Telegram id + order id larni tekshiradi va kerakli o'zgarishlarni DB da qiladi.
#     """
#
#     try:
#         user = User.objects.get(id=user_id)
#     except User.DoesNotExist:
#         return {"error": "User not found"}
#
#     cutoff_time = now() - timedelta(days=2, hours=12)
#
#     # 1️⃣ Telegram va order ID larni yig'ish
#     all_telegram_ids = [item["telegram_id"] for item in data]
#     all_order_ids = [ch["order_id"] for item in data for ch in item["channels"]]
#
#     base_qs = OrderMember.objects.filter(user_id=user_id, is_active=True)
#
#     # 2️⃣ Faqat kerakli a'zolarni olish
#     qs = base_qs.filter(
#         telegram__telegram_id__in=all_telegram_ids,
#         order_id__in=all_order_ids,
#          paid=PaidEnum.PENDING, joined_at__lt=cutoff_time
#     )
#
#     # 3️⃣ VIP yig'ish va bir vaqtning o'zida update qilish
#     user_vip = qs.aggregate(total_vip=Sum("vip"))["total_vip"] or 0
#     updated_count = qs.update(paid=PaidEnum.PAID)
#
#     # 4️⃣ Balansni yangilash
#     if user_vip > 0 and hasattr(user, "user_balance"):
#         from django.db import transaction
#         with transaction.atomic():
#             user.user_balance.balance = F("balance") + user_vip
#             user.user_balance.save(update_fields=["balance"])
#
#
#     return {
#         "updated_count": updated_count,
#         "balance_added": user_vip,
#         "message": f"{updated_count} ta a'zo yangilandi, {user_vip} VIP qo'shildi"
#     }


@shared_task
def process_telegram_checklist(data, user_id):
    """
    Yangi optimallashtirilgan versiya:
    - pair_key orqali kombinatsiya nazorati
    - 1 tranzaksiyada hamma update
    - Juda kam querylar (~5-6 ta)
    """

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return {"error": "User not found"}

    cutoff_time = now() - timedelta(days=2, hours=12)

    # pair_keylarni hosil qilamiz: "telegram_id:channel_id"
    all_pair_keys = {
        f"{item['telegram_id']}:{ch['channel_id']}"
        for item in data for ch in item["channels"]
    }
    tttt = [
        f"{item['telegram_id']}:{ch['channel_id']}"
        for item in data for ch in item["channels"]
    ]
    print(85, f"tttt: {tttt}")
    print(81, f"all_pair_keys {all_pair_keys}")
    # Agar bo‘sh kelsa — barcha aktivlarni o‘chir
    if not all_pair_keys:
        base_qs = OrderMember.objects.filter(user_id=user_id, is_active=True)
        with transaction.atomic():
            locked = base_qs.select_for_update(of=("self",))
            locked.update(
                is_active=False,
                paid=Case(
                    When(paid=PaidEnum.PENDING, then=Value(PaidEnum.UNPAID)),
                    default=F("paid"),
                )
            )
        return {
            "message": "IDs not provided. All active members set inactive and pending ones marked as UNPAID."
        }

    base_qs = OrderMember.objects.filter(user_id=user_id, is_active=True)
    al = [o.pair_key for o in base_qs]
    print(100, f"pair key {al}")

    # 1️⃣ PAID bo‘lishi kerak bo‘lgan yozuvlar
    paid_qs = base_qs.filter(
        pair_key__in=all_pair_keys,
        paid=PaidEnum.PENDING,
        joined_at__lt=cutoff_time
    )
    print(108, f"paid qs {paid_qs}")

    # 2️⃣ Faol qoladiganlar (pair_key bor)
    keep_active_ids = base_qs.filter(pair_key__in=all_pair_keys).values_list("id", flat=True)
    print(112, f"keep_active_ids {keep_active_ids}")

    with transaction.atomic():
        locked = base_qs.select_for_update(of=("self",))

        # VIP yig‘ish
        vip_sum = paid_qs.aggregate(total_vip=Sum("vip"))["total_vip"] or 0
        paid_ids = paid_qs.values_list("id", flat=True)

        # 3️⃣ Bulk update: paid va is_active
        locked.update(
            paid=Case(
                When(id__in=paid_ids, then=Value(PaidEnum.PAID)),
                default=F("paid"),
            ),
            is_active=Case(
                When(~Q(id__in=keep_active_ids), then=Value(False)),
                default=F("is_active"),
            ),
        )

        # 4️⃣ Balansni yangilash
        if vip_sum > 0 and hasattr(user, "user_balance"):
            type(user.user_balance).objects.filter(pk=user.user_balance.pk).update(
                balance=F("balance") + vip_sum
            )

    # 5️⃣ Pending, lekin endi inactive bo‘lganlarni unpaidga o‘tkaz
    OrderMember.objects.filter(
        user_id=user_id,
        is_active=False,
        paid=PaidEnum.PENDING
    ).update(paid=PaidEnum.UNPAID)

    return {
        "updated_paid_count": paid_qs.count(),
        "balance_added": vip_sum,
        "message": f"{paid_qs.count()} ta a'zo PAID qilindi, {vip_sum} VIP balansga qo‘shildi. Qolganlari unsubscribed qilindi."
    }


@shared_task
def delete_expired_subscriptions():
    cutoff_time = now() - timedelta(days=5)
    qs = OrderMember.objects.filter(is_active=True, joined_at__lt=cutoff_time)
    count = qs.count()
    qs.update(paid=PaidEnum.UNPAID)
    return {"deleted": count}


@shared_task(bind=True)
def backfill_telegram_links(self, service_id, user_id, url, count, channel_name, channel_id):
    """
    Telegramdan xabarlarni olib kelib DBga saqlash.
    Progressni kuzatish uchun self.update_state() ishlatamiz.
    """

    try:
        service = Service.objects.get(pk=service_id)
    except Service.DoesNotExist:
        return {"error": "Service not found."}

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return {"error": "User not found."}

    try:
        self.update_state(state="FETCHING")
        urls = async_to_sync(fetch_prior_message_urls)(url, count)
    except Exception as e:
        return {"error": f"Telegram fetch error: {e}"}

    self.update_state(state="SAVING")
    save_result = save_links_for_order(service, user, urls, channel_name, channel_id)

    return {
        "requested": count,
        "fetched_from_telegram": len(urls),
        "saved_to_db": save_result["created"],
        "already_in_db": save_result["existing"],
    }
