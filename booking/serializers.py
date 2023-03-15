from datetime import timedelta

from django.utils import timezone
from django.utils.timezone import make_aware

from rest_framework import exceptions, serializers
from rest_framework.serializers import ValidationError

from booking.models import Booking, BookingReview, Promocode, Transaction
from booking.services import BookingService
from notifications.service import NotifyService
from users.enums import AccountTypes
from users.models import BookerProfile, DJProfile
from users.serializers import UserSerializer
from users.services.payment import PaymentService
from utils.email import (send_booking_accepted_by_dj_to_booker,
                         send_booking_accepted_by_dj_to_dj,
                         send_booking_canceled_by_booker_to_booker,
                         send_booking_canceled_by_booker_to_dj,
                         send_booking_canceled_by_dj_to_booker,
                         send_booking_canceled_by_dj_to_dj,
                         send_booking_declined_by_dj_to_booker,
                         send_booking_declined_by_dj_to_dj)


class BookingDJProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    account_type = serializers.CharField(required=False, read_only=True)

    class Meta:
        model = DJProfile
        fields = '__all__'


class BookingBookerProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    account_type = serializers.CharField(required=False, read_only=True)

    class Meta:
        model = BookerProfile
        fields = '__all__'


class BookingSerializer(serializers.ModelSerializer):

    dj_profile = BookingDJProfileSerializer(read_only=True)
    booker_profile = BookingBookerProfileSerializer(read_only=True)

    music_list = serializers.ListField(required=True, allow_empty=False)
    listeners = serializers.ListField(required=True, allow_empty=False)

    performer_cost = serializers.FloatField(read_only=True)
    setup_time_cost = serializers.FloatField(read_only=True)
    performance_cost = serializers.FloatField(read_only=True)
    total_cost = serializers.FloatField(read_only=True)

    class Meta:
        model = Booking
        fields = '__all__'
        read_only_fields = [
            'id',
            'account_booker',
            'status',
            'decline_comment',
            'created_at',
            'updated_at',
            'payment_intent_client_secret',
            'dj_profile',
            'booker_profile',
            'adjustment_minutes',
            'dj_fee',
            'booker_fee',
            'performer_cost',
            'setup_time_cost',
            'performance_cost',
            'total_cost',
            'can_be_rated',
            'sum_to_pay_from_balance',
            'sum_to_pay_from_card'
        ]

    def validate(self, attrs):

        attrs['account_booker'] = self.context['request'].user.get_account()

        proposed_dates = set(Booking.objects.calculate_busy_dates(
            attrs['date'], attrs['time'], attrs['duration']))
        busy_dates = Booking.objects.get_busy_dates_for_dj(attrs['account_dj'])
        intersection = proposed_dates.intersection(set(busy_dates))
        if intersection:
            values = ','.join(sorted([v.strftime("%Y-%m-%d") for v in intersection]))
            raise exceptions.ValidationError({"date": f"Date(s) {values} already taken"})

        proposed_dt = make_aware(timezone.datetime.strptime(
            f"{str(attrs['date'])} {str(attrs['time'])}", '%Y-%m-%d %H:%M:%S'))
        if (proposed_dt - timezone.now()) <= timedelta(hours=2):
            raise exceptions.ValidationError(
                {"date": "Date and time of the event should be more than 2 hours from now"})

        return super().validate(attrs)

    def create(self, validated_data):
        booking = super().create(
            dict(**validated_data, price_per_hour=validated_data.get('account_dj').dj_profile.price_per_hour)
        )
        booking.save()
        return booking

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret['dj_profile'] = {
            **BookingDJProfileSerializer(instance.account_dj.dj_profile).data,
            'account_type': AccountTypes.DJ.value
        }
        if instance.account_booker.get_type() == AccountTypes.BOOKER.value:
            ret['booker_profile'] = {
                **BookingBookerProfileSerializer(instance.account_booker.booker_profile).data,
                'account_type': AccountTypes.BOOKER.value
            }
        else:
            ret['booker_profile'] = {
                **BookingDJProfileSerializer(instance.account_booker.dj_profile).data,
                'account_type': AccountTypes.DJ.value
            }

        ret['ext_status'] = instance.ext_status_text()

        # checkout page-related
        ret['performer_cost'] = round(instance.duration / 60 * instance.price_per_hour, 2)
        ret['setup_time_cost'] = instance.price_per_hour
        ret['performance_cost'] = round(instance.get_price() - instance.booker_fee, 2)
        ret['total_cost'] = instance.get_price()
        ret['duration_in_hours'] = instance.duration_in_hours
        ret['discounts'] = instance.get_discounts_sum()

        # show sum which is available to pay for this booking on the balance
        if instance.status == Booking.Status.NOT_PAID:

            booker = instance.account_booker
            booker_balance = Transaction.objects.get_user_balance(
                booker.user
            )

            if booker_balance >= PaymentService()._convert_amount_to_cents(instance.get_price()):
                sum_to_pay_from_balance = instance.get_price()
                sum_to_pay_from_card = 0
            else:
                sum_to_pay_from_balance = PaymentService()._convert_amount_to_dollars(
                    booker_balance
                )
                sum_to_pay_from_card = instance.get_price() - sum_to_pay_from_balance

            ret['sum_to_pay_from_balance'] = sum_to_pay_from_balance
            ret['sum_to_pay_from_card'] = sum_to_pay_from_card

        return ret


class BookingPaymentSerializer(serializers.Serializer):

    def validate(self, attrs):
        if self.instance.status != Booking.Status.NOT_PAID:
            raise ValidationError({'booking': 'Booking is already paid'})
        return super().validate(attrs)

    def pay_for_booking(self, instance):

        booking = instance

        booker = booking.account_booker
        booker_balance = Transaction.objects.get_user_balance(
            booker.user
        )

        if booker_balance >= PaymentService()._convert_amount_to_cents(booking.get_price()):
            sum_to_pay_from_balance = booking.get_price()
            sum_to_pay_from_card = 0
        else:
            sum_to_pay_from_balance = PaymentService()._convert_amount_to_dollars(
                booker_balance
            )
            sum_to_pay_from_card = booking.get_price() - sum_to_pay_from_balance

        booking.sum_to_pay_from_balance = sum_to_pay_from_balance
        booking.sum_to_pay_from_card = sum_to_pay_from_card

        # card payment is necessary (partial or no payment from user balance)
        if sum_to_pay_from_card > 0:

            payment_intent = PaymentService().create_payment_intent(
                booking=booking,
                amount=sum_to_pay_from_card
            )
            booking.payment_intent_id = payment_intent.stripe_id
            booking.payment_intent_client_secret = payment_intent['client_secret']
            booking.save()

        else:
            booking.save()

            PaymentService().pay_booking_from_balance(
                booking=booking
            )

        return {
            'id': booking.pk,
            'payment_intent_id': booking.payment_intent_id,
            'payment_intent_client_secret': booking.payment_intent_client_secret,
            'status': booking.status
        }


class BookingPromocodeApplicationSerializer(serializers.Serializer):

    promocode = serializers.CharField(required=True)

    def validate(self, attrs):

        try:
            promocode = Promocode.objects.get(code=attrs['promocode'], is_active=True)
        except Promocode.DoesNotExist:
            raise ValidationError({'promocode': "Promocode does not exist or is not valid"})

        attrs['promocode'] = promocode

        if self.instance in promocode.bookings.all():
            raise ValidationError({'promocode': "Promocode is already used for this booking"})

        if promocode.bookings.count() == promocode.max_application_count:
            raise ValidationError({'promocode': "Promocode has been used too many times."})

        if promocode.start_date and promocode.start_date >= timezone.now():
            raise ValidationError({
                'promocode':
                f"Promocode can't be used as it's not active before {promocode.start_date}"
            })

        if promocode.end_date and promocode.end_date <= timezone.now():
            raise ValidationError({
                'promocode':
                f"Promocode can't be used as it's not active after {promocode.end_date}"
            })

        return super().validate(attrs)

    def apply(self, instance):
        promocode = self.validated_data['promocode']
        promocode.bookings.add(instance)

        discount_data = {
            'promocode_type': promocode.promocode_type,
            'amount': promocode.amount,
            'price': instance.get_price(),
            'calculated_amount': promocode.get_calculated_amount(
                instance.get_price()
            ),
            'date': timezone.now().strftime("%d-%b-%Y (%H:%M:%S.%f)")
        }
        if not instance.applied_discounts:
            instance.applied_discounts = {}
        instance.applied_discounts[promocode.pk] = discount_data
        instance.save()

        return promocode


class BookingDeclineSerializer(serializers.ModelSerializer):
    decline_comment = serializers.CharField(required=True, min_length=300)

    class Meta:
        model = Booking
        fields = ['status', 'decline_comment']
        read_only_fields = ['status']

    def decline(self, instance, decline_comment):
        account = self.context['request'].user.get_account()
        if instance.status in [Booking.Status.NOT_PAID, Booking.Status.PAID]:
            if instance.status == Booking.Status.PAID:
                PaymentService().refund_booking(
                    booking=instance,
                    account=self.context['request'].user.get_account()
                )
            instance.decline(
                account=account,
                decline_comment=decline_comment
            )
            instance.save()
            if account == instance.account_booker:
                # we use canceled emails instead of decline here
                send_booking_canceled_by_booker_to_dj(
                    emails=[instance.account_dj.user.email],
                    booking=instance
                )
                send_booking_canceled_by_booker_to_booker(
                    emails=[instance.account_booker.user.email],
                    booking=instance
                )
                NotifyService().create_notify_booking_declined(
                    account=instance.account_dj,
                    booking=instance
                )
                NotifyService().create_notify_booking_declined(
                    account=instance.account_booker,
                    booking=instance
                )
            elif account == instance.account_dj:
                send_booking_declined_by_dj_to_dj(
                    emails=[instance.account_dj.user.email],
                    booking=instance
                )
                send_booking_declined_by_dj_to_booker(
                    emails=[instance.account_booker.user.email],
                    booking=instance
                )
                NotifyService().create_notify_booking_declined(account=instance.account_booker,
                                                               booking=instance)
                NotifyService().create_notify_booking_declined(account=instance.account_dj,
                                                               booking=instance)


class BookingCancelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['status']
        read_only_fields = ['status']

    def update(self, instance, validated_data):
        account = self.context['request'].user.get_account()
        if instance.status in [Booking.Status.ACCEPTED_BY_DJ]:
            instance.cancel(self.context['request'].user.get_account())
            instance.save()
            if account == instance.account_booker:
                send_booking_canceled_by_booker_to_dj(
                    emails=[instance.account_dj.user.email],
                    booking=instance
                )
                send_booking_canceled_by_booker_to_booker(
                    emails=[instance.account_booker.user.email],
                    booking=instance
                )
                NotifyService().create_notify_booking_canceled(
                    account=instance.account_dj,
                    booking=instance
                )
                NotifyService().create_notify_booking_canceled(
                    account=instance.account_booker,
                    booking=instance
                )
            elif account == instance.account_dj:
                send_booking_canceled_by_dj_to_booker(
                    emails=[instance.account_booker.user.email],
                    booking=instance
                )
                send_booking_canceled_by_dj_to_dj(
                    emails=[instance.account_dj.user.email],
                    booking=instance
                )
                NotifyService().create_notify_booking_canceled(
                    account=instance.account_booker,
                    booking=instance
                )
                NotifyService().create_notify_booking_canceled(
                    account=instance.account_dj,
                    booking=instance
                )
        return super().update(instance, validated_data)


class BookingAcceptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['status']
        read_only_fields = ['status']

    def accept(self, instance):
        instance.accept()
        instance.save()
        send_booking_accepted_by_dj_to_dj(
            emails=[instance.account_dj.user.email],
            booking=instance
        )
        send_booking_accepted_by_dj_to_booker(
            emails=[instance.account_booker.user.email],
            booking=instance
        )
        NotifyService().create_notify_booking_accepted_by_dj(
            account=instance.account_booker, booking=instance)
        NotifyService().create_notify_booking_accepted_by_dj(
            account=instance.account_dj, booking=instance)


class BookingReportFileSerializer(serializers.Serializer):
    file_name = serializers.CharField(read_only=True)

    class Meta:
        fields = ['file_name']
        read_only_fields = ['file_name']

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass

    def create_booking_report_file(self, instance):
        file_name = BookingService().create_booking_report_file(instance)
        return file_name


class BookingReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingReview
        fields = '__all__'
        read_only_fields = ['booking']

    def validate(self, attrs):
        try:
            booking = Booking.objects.get(pk=self.context["view"].kwargs["pk"])
        except:
            raise ValidationError({'booking': "Booking does not exists"})
        attrs['booking'] = booking
        return super().validate(attrs)
