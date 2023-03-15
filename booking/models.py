import datetime
import random
import string
from datetime import date, time, timedelta

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.timezone import make_aware
from django.utils.translation import gettext_lazy as _

from booking.enums import PromocodeType, TransactionPurposes
from model_utils.tracker import FieldTracker
from notifications.service import NotifyService
from users.enums import AccountTypes, GigTypes, Music
from users.errors import NotEnoughBalanceForDecrease
from utils.email import send_booking_completed, send_booking_paid, send_booking_success


class BookingQuerySet(models.QuerySet):

    def by_account(self, account):
        return self.filter(Q(account_booker=account) | Q(account_dj=account))

    def by_booker(self, account):
        return self.filter(account_booker=account)

    def by_dj(self, account):
        return self.filter(account_dj=account)

    def by_dj_and_booker(self, dj_account, booker_account):
        return self.filter(account_booker=booker_account, account_dj=dj_account)

    def filter_before_and_including_date(self, date):
        return self.filter(date__lte=date)


class BookingManager(models.Manager):

    def get_queryset(self):
        return BookingQuerySet(self.model, using=self._db)

    def filter_by_account(self, account):
        return self.get_queryset().by_account(account=account)

    def filter_by_booker(self, account):
        return self.get_queryset().by_booker(account=account)

    def filter_by_dj(self, account):
        return self.get_queryset().by_dj(account=account)

    def filter_by_dj_and_booker_accounts(self, booker_account, dj_account):
        return self.get_queryset().by_dj_and_booker(dj_account=dj_account,
                                                    booker_account=booker_account)

    def get_paid(self):
        return self.get_queryset().filter(status=self.model.Status.PAID)

    def get_not_paid(self):
        return self.get_queryset().filter(status=self.model.Status.NOT_PAID)

    def get_success(self):
        return self.get_queryset().filter(status=self.model.Status.SUCCESS)

    def get_completed(self):
        return self.get_queryset().filter(status=self.model.Status.COMPLETED)

    def get_canceled(self):
        return self.get_queryset().filter(
            status__in=[self.model.Status.CANCELED_BY_DJ, self.model.Status.CANCELED_BY_BOOKER]
        )

    def get_disputed(self):
        return self.get_queryset().filter(
            status__in=[self.model.Status.IN_DISPUTE, self.model.Status.DISPUTED]
        )

    def get_success_by_booker_account(self, account):
        return self.get_success().filter(account_booker=account)

    def get_accepted_by_dj(self):
        return self.get_queryset().filter(status=self.model.Status.ACCEPTED_BY_DJ)

    def calculate_busy_dates(self, date: date, time: time, duration: int):
        """
        Get list of dates when DJ will be busy for this gig
        """
        end_date = datetime.datetime.combine(date, time) + timedelta(minutes=duration)
        busy_dates = [date + timedelta(days=x)
                      for x in range((end_date.date() - date).days + 1)]
        return busy_dates

    def get_busy_dates_for_dj(self, account_dj: 'Account'):

        if account_dj.get_type() != AccountTypes.DJ.value:
            raise ValidationError("DJ Account expected")

        BUSY_STATUSES = [
            Booking.Status.NOT_PAID,
            Booking.Status.PAID,
            Booking.Status.ACCEPTED_BY_DJ,
            Booking.Status.COMPLETED,
            Booking.Status.SUCCESS
        ]

        busy_dates = []
        for booking in account_dj.bookings_dj.filter(status__in=BUSY_STATUSES):
            busy_dates += Booking.objects.calculate_busy_dates(
                booking.date, booking.time, booking.duration)

        return sorted(list(set(busy_dates)))


class Booking(models.Model):

    class Listener(models.IntegerChoices):
        YOUNGER17 = 1, _('17 and Younger')
        L18_35 = 2, _('18-35')
        L36_50 = 3, _('36-50')
        OLDER50 = 4, _('50 and Older')

    class Status(models.IntegerChoices):
        NOT_PAID = 1, _('Not paid')
        PAID = 2, _('Paid')
        DECLINED_BY_BOOKER = 3, _('Declined by booker')
        DECLINED_BY_DJ = 4, _('Declined by DJ')
        SUCCESS = 5, _('Success')
        COMPLETED = 6, _('Completed')
        ACCEPTED_BY_DJ = 7, _('Accepted by DJ')
        CANCELED_BY_BOOKER = 8, _('Canceled by booker')
        CANCELED_BY_DJ = 9, _('Canceled by DJ')
        REJECTED = 10, _('Rejected'),
        DECLINED_BY_STAFF = 11, _('Declined by staff')
        IN_DISPUTE = 12, _("In dispute")
        DISPUTED = 13, _("Disputed")

    TYPES = [
        (GigTypes.PARTY.value, 'Party'),
        (GigTypes.WEDDING.value, 'Wedding'),
        (GigTypes.RADIO_PLAY.value, 'Radio Play'),
        (GigTypes.BIRTHDAY_PARTY.value, 'Birthday Party'),
        (GigTypes.MITZVAH.value, 'Mitzvah'),
        (GigTypes.BAR_NIGHT.value, 'Bar Night'),
        (GigTypes.CLUB.value, 'Club'),
        (GigTypes.RELIGIOUS_EVENT.value, 'Religious Event'),
        (GigTypes.PICNIC.value, 'Picnic'),
        (GigTypes.FAMILY_REUNION.value, 'Family Reunion'),
        (GigTypes.OTHER.value, 'Other'),
    ]
    OUT_DOOR_PLAYS = [
        (1, 'Radio Play'),
        (2, 'Picnic Area'),
        (3, '**Other'),
        (4, 'Not Applicable')
    ]
    IN_DOOR_PLAYS = [
        (1, '1000 Squ/ft or Less'),
        (2, '1400 Squ/ft'),
        (3, '1600 Squ/ft'),
        (4, '2000 Squ/ft'),
        (5, '2200 Squ/ft'),
        (6, '2500 Squ/ft'),
        (7, '3000 Squ/ft'),
        (8, '3100 Squ/ft'),
        (9, '3500 Squ/ft and More'),
    ]
    SERVICE_FEE_PERCENT_FOR_BOOKER = 5
    SERVICE_FEE_PERCENT_FOR_DJ = 10
    MIN_PRICE = 350
    MIN_DURATION_IN_HOURS = 2  # 1 hour + 1 setup hour (0.5 before and 0.5 after)

    account_booker = models.ForeignKey(
        'users.Account',
        on_delete=models.CASCADE,
        related_name='bookings_booker'
    )
    account_dj = models.ForeignKey(
        'users.Account',
        on_delete=models.CASCADE,
        related_name='bookings_dj'
    )
    comment = models.TextField('comment', max_length=300)
    location_state = models.CharField('state', max_length=100, null=True)
    location_city = models.CharField('city', max_length=100, null=True)

    date = models.DateField('event date', null=True)
    time = models.TimeField('event time', null=True)
    duration = models.PositiveIntegerField('gig duration, in minutes')
    dj_busy_dates = ArrayField(models.DateField(), null=True, blank=True)

    event_address = models.CharField('Event Address', max_length=500, null=True, blank=True)

    # payment-related
    price_per_hour = models.FloatField(
        verbose_name=_('Price per hour in $_'),
        blank=False,
        null=False,
        default=0,
    )
    adjustment_minutes = models.PositiveIntegerField(
        'Adjusted gig minutes added',
        blank=True,
        null=True,
        default=0
    )
    booker_fee = models.FloatField(
        verbose_name=_('Booker fee in $'),
        blank=True,
        null=True,
        default=0
    )
    booker_fee_percent = models.PositiveIntegerField(
        'Percent used for Booker fee',
        blank=True,
        null=True,
        default=0
    )
    dj_fee = models.FloatField(
        verbose_name=_('DJ fee in $'),
        blank=True,
        null=True,
        default=0
    )
    dj_fee_percent = models.PositiveIntegerField(
        'Percent used for DJ fee',
        blank=True,
        null=True,
        default=0
    )
    payment_intent_id = models.CharField(
        'payment intent ID',
        max_length=200,
        editable=False
    )
    payment_intent_client_secret = models.CharField(
        'payment intent client secret',
        max_length=200,
        editable=False
    )
    sum_to_pay_from_balance = models.FloatField(
        verbose_name=_('Sum to pay from balance (not stripe) in $'),
        blank=True,
        null=True,
        default=0
    )
    sum_to_pay_from_card = models.FloatField(
        verbose_name=_('Sum to pay from card in $'),
        blank=True,
        null=True,
        default=0
    )
    applied_discounts = models.JSONField(
        verbose_name='Applied discounts',
        null=True,
        blank=True
    )

    gig_type = models.IntegerField('gig type', choices=TYPES)
    music_list = ArrayField(
        base_field=models.IntegerField(choices=Music.choices),
        size=None,
        verbose_name='music list',
        null=True
    )
    listeners = ArrayField(
        base_field=models.IntegerField(choices=Listener.choices),
        size=None,
        verbose_name='listeners',
        null=True
    )
    clean_mix = models.BooleanField('clean mix?', null=True)
    virtual_mix = models.BooleanField('virtual mix?', null=True)
    add_ons_microphone = models.BooleanField('extra microphone?', null=True)
    add_ons_fog_machine = models.BooleanField('extra fog machine?', null=True)
    add_ons_power_cords = models.BooleanField('extra power cords?', null=True)
    add_ons_speakers = models.BooleanField('extra speakers?', null=True)
    out_door_play = models.IntegerField('out_door_play', choices=OUT_DOOR_PLAYS)
    in_door_play = models.IntegerField('in_door_play', choices=IN_DOOR_PLAYS)

    status = models.IntegerField('status', choices=Status.choices, default=Status.NOT_PAID)
    decline_comment = models.TextField('decline comment', blank=True, null=True)

    can_be_rated = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    objects = BookingManager()

    TRACKABLE_FIELDS = ['status', 'decline_comment']

    tracker = FieldTracker(fields=TRACKABLE_FIELDS)

    class Meta:
        verbose_name = 'Booking'
        verbose_name_plural = 'Bookings'

    def save(self, *args, **kwargs):
        self.adjustment_minutes = self._get_adjustment_minutes()

        self.booker_fee = self._get_booker_fee()
        self.booker_fee_percent = Booking.SERVICE_FEE_PERCENT_FOR_BOOKER

        self.dj_fee = self._get_dj_fee()
        self.dj_fee_percent = Booking.SERVICE_FEE_PERCENT_FOR_DJ

        self.dj_busy_dates = Booking.objects.calculate_busy_dates(
            self.date, self.time, self.duration)

        super().save(*args, **kwargs)

    def _get_adjustment_minutes(self) -> int:
        """Additional gig time included in minimum price, in minutes."""
        adjustment_sum = Booking.MIN_PRICE - self._get_price_not_adjusted()
        if adjustment_sum > 0:
            return adjustment_sum / self.price_per_hour * 60
        return 0

    def _get_booker_fee(self) -> float:
        return self.get_price() * Booking.SERVICE_FEE_PERCENT_FOR_BOOKER / 100

    def _get_dj_fee(self) -> float:
        return self.get_price() * Booking.SERVICE_FEE_PERCENT_FOR_DJ / 100

    def _get_price_not_adjusted(self) -> float:
        """Price for booked hours + 1 setup hour."""
        return (self.duration / 60 + 1) * self.price_per_hour

    def get_price(self) -> float:
        """Calculated price:
        1) adjust for required minimal price. In $
        2) calculate discount from promocodes
        """
        calculated_price = self._get_price_not_adjusted()

        if calculated_price < Booking.MIN_PRICE:
            price = float(Booking.MIN_PRICE)
        else:
            price = calculated_price

        price = price - self.get_discounts_sum()

        return round(price, 2)

    def get_discounts_sum(self) -> float:
        """Total sum of discounts applied to booking."""

        discounts_sum = 0

        if self.applied_discounts:
            for promocode_id, data in self.applied_discounts.items():
                discounts_sum += data['calculated_amount']

        return discounts_sum

    def get_dj_earnings(self) -> float:
        return round(self.get_price() - self.dj_fee - self.booker_fee, 1)

    def get_datetime(self):
        dt = timezone.datetime.strptime(
            f'{str(self.date)} {str(self.time.replace(microsecond=0))}',
            '%Y-%m-%d %H:%M:%S'
        )
        return make_aware(dt)

    def decline(self, account, decline_comment):
        if account == self.account_booker:
            self.status = self.Status.DECLINED_BY_BOOKER
        elif account == self.account_dj:
            self.status = self.Status.DECLINED_BY_DJ
        else:
            self.status = self.Status.DECLINED_BY_STAFF
        self.decline_comment = decline_comment

    def cancel(self, account):
        if account == self.account_booker:
            self.status = self.Status.CANCELED_BY_BOOKER
        else:
            self.status = self.Status.CANCELED_BY_DJ

    def accept(self):
        self.status = self.Status.ACCEPTED_BY_DJ

    def pay(self):
        self.status = self.Status.PAID
        self.save()
        send_booking_paid(emails=[self.account_dj.user.email, self.account_booker.user.email])
        NotifyService().create_notify_booking_paid(account=self.account_dj, booking=self)
        NotifyService().create_notify_booking_paid(account=self.account_booker, booking=self)

    def success(self):
        self.status = self.Status.SUCCESS
        self.save()
        send_booking_success(
            emails=[self.account_dj.user.email, self.account_booker.user.email],
            booking=self
        )
        NotifyService().create_notify_booking_success(account=self.account_dj, booking=self)
        NotifyService().create_notify_booking_success(account=self.account_booker, booking=self)

    def completed(self):
        self.status = self.Status.COMPLETED
        self.save()
        send_booking_completed(
            emails=[self.account_dj.user.email],
            booking=self
        )
        NotifyService().create_notify_booking_completed(
            account=self.account_dj, booking=self)

    def reject(self):
        self.status = self.Status.REJECTED
        self.save()

    def set_status_dispute(self):
        self.status = self.Status.IN_DISPUTE

    def set_status_disputed(self):
        self.status = self.Status.DISPUTED

    @property
    def gig_type_value(self) -> str:
        return GigTypes(self.gig_type).label

    @property
    def duration_in_hours(self) -> float:
        return round(self.duration / 60, 1)

    def ext_status_text(self, status: int = None) -> str:
        """Returns status text including related dispute text if it exists."""

        from dispute.models import Dispute

        if status is not None:
            try:
                status = int(status)
            except ValueError:
                status = None
        else:
            status = self.status

        if status in [Booking.Status.IN_DISPUTE, Booking.Status.DISPUTED]:
            try:
                dispute = Dispute.objects.get(booking=self)
            except Dispute.DoesNotExist:
                ext_status = 'No dispute'
            else:
                if dispute.status == 1:
                    ext_status = "Dispute Open. Booker Provided Testimony"
                if dispute.status == 2:
                    ext_status = "Performer Provided Testimony"
                if dispute.status == 3:
                    ext_status = "Timed Out/Performer Concede"
                if dispute.status == 6:
                    ext_status = "Timed Out/Booker Concede"
                if dispute.status == 4:
                    ext_status = "Resolved by staff to booker favor"
                if dispute.status == 5:
                    ext_status = "Ruled in favor of Performer"

            bookings_status_text = [i.label for i in Booking.Status if status == i][0]
            return f'{bookings_status_text}: {ext_status}'

        statuses = [i.label for i in Booking.Status if status == i]
        return statuses[0] if statuses else None

    def __str__(self):

        return 'Booking #{0} (DJ: {1}, Booker: {2}, Date: {3}, Status: {4} ({5}))'.format(
            self.pk,
            self.account_dj,
            self.account_booker,
            self.get_datetime().replace(microsecond=0).strftime("%d-%b-%Y (%H:%M:%S)"),
            self.status,
            self.ext_status_text()
        )


class BookingChangeRecord(models.Model):

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name='change_records',
        blank=False,
        null=False
    )

    field_name = models.CharField(
        'Field',
        max_length=100,
        null=True
    )

    value_before = models.CharField(
        'Value Before',
        max_length=2048,
        null=True,
        blank=True
    )

    value_after = models.CharField(
        'Value After',
        max_length=2048,
        null=True,
        blank=True
    )

    is_by_staff = models.BooleanField(
        'By Staff',
        default=False
    )

    author = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='user_booking_change_records',
        blank=False,
        null=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        editable=False
    )

    def __str__(self):
        return '{0}: {1} {2} {3} {4} {5}'.format(
            self.pk,
            self.booking,
            self.field_name,
            self.value_before,
            self.value_after,
            self.created_at.strftime("%d-%b-%Y (%H:%M:%S.%f)")
        )


class BookingReview(models.Model):

    rating = models.PositiveSmallIntegerField(
        'rating',
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    booking = models.ForeignKey(
        'Booking',
        on_delete=models.CASCADE,
        related_name='reviews'
    )
    comment = models.CharField(
        'comment',
        max_length=250,
        blank=True,
        null=True
    )
    is_by_booker = models.BooleanField(default=True)
    created_at = models.DateTimeField(
        auto_now_add=True,
        editable=False
    )


class TransactionQuerySet(models.QuerySet):

    def filter_by_user(self, user_pk):
        return self.filter(user__id=user_pk)

    def filter_not_hold(self):
        return self.filter(is_hold=False)

    def calc_amount_sum(self):
        return self.aggregate(Sum('amount')).get('amount__sum')


class TransactionManager(models.Manager):
    def get_queryset(self):
        return TransactionQuerySet(self.model, using=self._db)

    @staticmethod
    def convert_to_negative_value(value: (float, int)):
        return value if value < 0 else value * (-1)

    @staticmethod
    def convert_to_positive_value(value: (float, int)):
        return value if value > 0 else value * (-1)

    def get_user_balance(self, user: 'User') -> float:
        return self.all().filter_by_user(user.pk).filter_not_hold().calc_amount_sum() or 0

    def is_exist_transaction(self, booking: Booking):
        return self.all().filter(
            amount=booking.get_price() * 100,
            user=booking.user,
            entity_pk=booking.pk
        )

    @staticmethod
    def __get_all_purposes() -> list:
        return [enum.value for enum in TransactionPurposes]

    @staticmethod
    def __get_user_balance_decrease_purposes() -> list:
        return [
            TransactionPurposes.BOOKING_ESCROW,
            TransactionPurposes.BOOKING_FEE_FOR_BOOKER,
            TransactionPurposes.BOOKING_FEE_FOR_DJ,
            TransactionPurposes.WITHDRAWAL
        ]

    def __check_user_balance_before_decrease(self, user: 'User', amount: int) -> bool:
        amount = self.convert_to_negative_value(amount)
        if self.get_user_balance(user) + amount < 0:
            return False
        return True

    def create_transaction(self, amount: int, user: 'User', purpose: int, entity=None, is_hold=False) -> 'Transaction':

        if purpose not in self.__get_all_purposes():
            raise ValidationError("Incorrect transaction purpose")

        if purpose in self.__get_user_balance_decrease_purposes():
            amount = self.convert_to_negative_value(amount)
            if not self.__check_user_balance_before_decrease(user, amount):
                raise NotEnoughBalanceForDecrease()

        if isinstance(entity, Booking):
            return self.update_or_create(
                amount=amount,
                user=user,
                entity=entity.__class__.__name__,
                entity_pk=entity.pk,
                purpose=purpose,
                defaults=dict(is_hold=is_hold)
            )
        elif isinstance(entity, Withdrawal):
            return self.update_or_create(
                amount=amount,
                user=user,
                entity=entity.__class__.__name__,
                entity_pk=entity.pk,
                purpose=purpose,
                defaults=dict(is_hold=is_hold)
            )
        else:
            raise Exception(
                f"Нет связанных целей (purpose) для изменения баланса на {entity.__class__}")


class Transaction(models.Model):

    objects = TransactionManager()

    user = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='transactions',
        editable=False
    )
    purpose = models.PositiveSmallIntegerField(
        verbose_name='Purpose',
        choices=TransactionPurposes.choices,
        blank=False,
        editable=False
    )
    amount = models.IntegerField(
        verbose_name='Amount',
        blank=False,
        editable=False,
        default=0
    )
    entity = models.CharField(
        verbose_name="Object Type",
        max_length=255,
        blank=True,
        null=True,
        editable=False
    )
    entity_pk = models.BigIntegerField(
        verbose_name='Object ID',
        blank=True,
        null=True,
        editable=False
    )
    is_hold = models.BooleanField(
        'Is hold',
        default=False,
        blank=True,
        null=False
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        editable=False
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return '{0}: {1} {2} {3} {4} {5}'.format(
            self.pk,
            self.user,
            self.amount,
            self.entity_pk,
            self.purpose,
            self.created_at.strftime("%d-%b-%Y (%H:%M:%S.%f)")
        )


class WithdrawalManager(models.Manager):

    def convert_to_cents(self, amount: float):
        return int(float(amount) * 100)


class Withdrawal(models.Model):
    """ Вывод денег со счета системы """

    class Status(models.IntegerChoices):
        IN_REVIEW = 1, _('In review')
        APPROVED = 2, _('Approved')
        REJECTED = 3, _('Rejected')
        CANCELED = 4, _('Canceled')

    user = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='withdrawals'
    )
    amount = models.IntegerField(
        verbose_name='Amount',
        blank=False,
        default=0
    )
    status = models.IntegerField(
        'status',
        choices=Status.choices,
        default=Status.IN_REVIEW
    )
    destination_card = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )
    result = models.CharField(
        max_length=1024,
        blank=True,
        null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = WithdrawalManager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return '{0}: {1} {2} {3} {4}'.format(
            self.pk,
            self.user,
            self.amount,
            self.status,
            self.created_at.strftime("%d-%b-%Y (%H:%M:%S.%f)")
        )


class Promocode(models.Model):

    code = models.CharField(
        'Code',
        max_length=255,
        null=False,
        unique=True,
        blank=True
    )

    promocode_type = models.IntegerField(
        'Promocode Type',
        choices=PromocodeType.choices
    )

    amount = models.FloatField(
        verbose_name='Amount',
        blank=False,
        null=False
    )

    max_application_count = models.PositiveSmallIntegerField(
        blank=True,
        null=True
    )
    start_date = models.DateTimeField(blank=True, null=True)
    end_date = models.DateTimeField(blank=True, null=True)

    is_active = models.BooleanField(default=True)

    bookings = models.ManyToManyField(
        Booking,
        related_name='promocodes_set',
        related_query_name='promocode',
        verbose_name='bookings'
    )

    updated_at = models.DateTimeField(auto_now=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def _generate_code(self) -> str:
        """Generates random A-Z0-9{10} code."""
        letters = string.ascii_letters + string.digits
        return "".join(random.choice(letters).upper() for i in range(10))  # nosec

    def get_calculated_amount(self, price: float) -> float:
        if self.promocode_type == PromocodeType.FIXED.value:
            return self.amount
        return round(self.amount * price / 100, 2)

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._generate_code()
        super().save(*args, **kwargs)

    def __str__(self):
        return '{0}: {1} {2} {3} {4}'.format(
            self.pk,
            self.code,
            self.amount,
            self.promocode_type,
            self.created_at.strftime("%d-%b-%Y (%H:%M:%S.%f)")
        )
