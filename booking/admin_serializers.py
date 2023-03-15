from django.db.models import Sum

from rest_framework import serializers
from rest_framework.serializers import ValidationError

from booking.enums import PromocodeType
from booking.models import (Booking,
                            BookingChangeRecord,
                            Promocode,
                            Transaction,
                            Withdrawal)
from notifications.service import NotifyService
from users.serializers import UserSerializer
from users.services.payment import PaymentService
from utils.email import (send_booking_declined_by_staff_to_booker,
                         send_booking_declined_by_staff_to_performer)


class BookingDeclineAdminSerializer(serializers.ModelSerializer):

    decline_comment = serializers.CharField(required=True, min_length=1)

    class Meta:
        model = Booking
        fields = ['status', 'decline_comment']
        read_only_fields = ['status']

    def decline(self, instance, decline_comment):
        account = self.context['request'].user.get_account()
        instance.decline(
            account=account,
            decline_comment=decline_comment
        )
        PaymentService().refund_booking(
            booking=instance,
            account=account
        )
        instance.save()
        send_booking_declined_by_staff_to_booker(
            emails=[instance.account_booker.user.email],
            booking=instance
        )
        send_booking_declined_by_staff_to_performer(
            emails=[instance.account_dj.user.email],
            booking=instance
        )
        NotifyService().create_notify_booking_declined_by_staff(
            account=instance.account_dj,
            booking=instance
        )
        NotifyService().create_notify_booking_declined_by_staff(
            account=instance.account_booker,
            booking=instance
        )


class WithdrawalAdminSerializer(serializers.ModelSerializer):

    user = UserSerializer(read_only=True)

    class Meta:
        model = Withdrawal
        fields = [
            'id',
            'user',
            'amount',
            'status',
            'result',
            'destination_card',
            'created_at'
        ]
        read_only_fields = [
            'id',
            'user',
            'amount',
            'status',
            'result',
            'destination_card',
            'created_at'
        ]


class TransactionAdminSerializer(serializers.ModelSerializer):

    user = UserSerializer(read_only=True)

    class Meta:
        model = Transaction
        fields = [
            'id',
            'user',
            'amount',
            'purpose',
            'entity',
            'entity_pk',
            'is_hold',
            'created_at'
        ]
        read_only_fields = [
            'id',
            'user',
            'amount',
            'purpose',
            'entity',
            'entity_pk',
            'is_hold',
            'created_at'
        ]

    def to_representation(self, instance):
        res = super().to_representation(instance)
        res['withdrawals'] = instance.amount if instance.amount < 0 else 0
        res['deposits'] = instance.amount if instance.amount > 0 else 0
        res['running_balance'] = Transaction.objects.filter(
            pk__lte=instance.pk
        ).aggregate(running_balance=Sum('amount'))['running_balance']
        return res


class BookingChangeRecordAdminSerializer(serializers.ModelSerializer):

    class Meta:
        model = BookingChangeRecord
        fields = [
            'id',
            'booking',
            'field_name',
            'value_before',
            'value_after',
            'created_at'
        ]
        read_only_fields = [
            'id',
            'booking',
            'field_name',
            'value_before',
            'value_after',
            'created_at'
        ]

    def to_representation(self, instance):
        res = super().to_representation(instance)
        res['value_before'] = instance.booking.ext_status_text(instance.value_before)
        res['value_after'] = instance.booking.ext_status_text(instance.value_after)
        return res


class PromocodeAdminSerializer(serializers.ModelSerializer):

    code = serializers.CharField(required=False)
    promocode_type = serializers.IntegerField(required=False)
    max_application_count = serializers.IntegerField(required=False, min_value=1)
    start_date = serializers.DateTimeField(required=False)
    end_date = serializers.DateTimeField(required=False)
    amount = serializers.FloatField(required=False)
    is_active = serializers.BooleanField(required=False)

    class Meta:
        model = Promocode
        fields = [
            'id',
            'code',
            'promocode_type',
            'amount',
            'max_application_count',
            'start_date',
            'end_date',
            'is_active',
            'updated_at',
            'created_at'
        ]
        read_only_fields = [
            'id',
            'bookings',
            'updated_at',
            'created_at'
        ]

    def validate(self, attrs):

        if 'promocode_type' in attrs:
            if attrs['promocode_type'] == PromocodeType.PERCENT.value:
                if attrs['amount'] <= 0:
                    raise ValidationError({'amount': 'Percent should not be less than 0'})
                if attrs['amount'] > 100:
                    raise ValidationError({'amount': 'Percent should not be more than 100'})

        return super().validate(attrs)
