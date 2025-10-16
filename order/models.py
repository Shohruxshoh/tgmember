from decimal import Decimal
from django.db import models
from django.db.models import Count, F, Value, Case, When, ExpressionWrapper, UniqueConstraint
from django.db.models import DecimalField
from rest_framework.exceptions import ValidationError

from order.enums import Status, PaidEnum
from service.models import Service
from users.models import User, TelegramAccount


# Create your models here.
class OrderQuerySet(models.QuerySet):
    def with_totals(self):
        """
        DB darajasida ikki annotation:
        - total_members_anno: members count
        - calculated_total: (member / price) * total_members_anno
          => price=0 bo'lsa 0
        """
        qs = self.annotate(
            total_members_anno=Count('members', distinct=True)
        )
        qs = qs.annotate(
            calculated_total=Case(
                When(
                    price__gt=0,
                    then=ExpressionWrapper(
                        (F('price') / F('member')) / F('total_members_anno'),
                        output_field=DecimalField(max_digits=20, decimal_places=2)
                    )
                ),
                default=Value(0),
                output_field=DecimalField(max_digits=20, decimal_places=2)
            )
        )
        return qs


class OrderManager(models.Manager.from_queryset(OrderQuerySet)):
    pass


class Order(models.Model):
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    link = models.CharField(max_length=200)
    channel_name = models.CharField(max_length=200)
    channel_id = models.BigIntegerField(default=0)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    member = models.PositiveIntegerField(default=0)
    service_category = models.CharField(max_length=200, null=True, blank=True, db_index=True)
    country_code = models.CharField(max_length=200, null=True, blank=True, db_index=True)
    day = models.PositiveIntegerField(default=0)
    number_added = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrderManager()

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "status"]),
        ]

    def __str__(self):
        return f"Order#{self.pk} by {self.user}"

    @property
    def self_members(self):
        """Related OrderMember yozuvlari soni."""
        return self.members.count()

    @property
    def calculated_total(self):
        """
        Python darajasida fallback:
        (member / price) * self_members
        price=0 -> 0
        """
        if not self.price:
            return Decimal('0')
        return (Decimal(self.price) / Decimal(self.member)) * Decimal(self.self_members)


class OrderMember(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='members')
    telegram = models.ForeignKey(TelegramAccount, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vip = models.PositiveIntegerField(default=0)
    paid = models.CharField(max_length=20, choices=PaidEnum.choices, default=PaidEnum.PENDING)
    member_duration = models.PositiveIntegerField(default=0)
    # is_subscription = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    # 🆕 Denormalizatsiya maydoni
    pair_key = models.CharField(max_length=100, db_index=True, editable=False)

    class Meta:
        constraints = [
            # faqatgina is_active=True bo‘lganda unique
            models.UniqueConstraint(
                fields=["order", "telegram", "user"],
                condition=models.Q(is_active=True),
                name="unique_active_order_member"
            )
        ]
        indexes = [
            # tez-tez ishlatiladigan querylar uchun composite index
            models.Index(fields=["order", "user", "telegram"]),
            # joined_at bo‘yicha range filter uchun
            models.Index(fields=["joined_at"]),
            # faollik bo‘yicha filter uchun
            models.Index(fields=["is_active"]),
            # 🆕 pair_key uchun index
            models.Index(fields=["pair_key"]),
        ]

    def clean(self):
        """
        Model darajasida validatsiya.
        Shunday order+telegram+user kombinatsiyasida boshqa active yozuv bormi, tekshiradi.
        """
        if self.is_active:
            qs = OrderMember.objects.filter(
                order=self.order,
                telegram=self.telegram,
                user=self.user,
                is_active=True,
            )
            if self.pk:  # agar update bo‘lsa, o‘zi chiqib ketadi
                qs = qs.exclude(pk=self.pk)

            if qs.exists():
                raise ValidationError(
                    "Bu order, telegram va user kombinatsiyasi uchun faqat bitta active yozuv bo‘lishi mumkin.")

    def prepare_save(self):
        if self.telegram and self.order:
            self.pair_key = f"{self.telegram.telegram_id}:{self.order.channel_id}"
        self.clean()

    def save(self, *args, **kwargs):
        self.prepare_save()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.email}- {self.order.channel_name}"
