from django import forms
from django.contrib import admin
from django.db.models import Sum
from django.utils.html import format_html

from admin_totals.admin import ModelAdminTotals
from booking.models import (Booking,
                            BookingChangeRecord,
                            BookingReview,
                            Promocode,
                            Transaction,
                            Withdrawal)
from users.enums import Music
from users.models import Account


class BookingCreationFormAdmin(forms.ModelForm):

    account_booker = forms.ModelChoiceField(
        queryset=Account.objects.all().get_booker_accounts())
    account_dj = forms.ModelChoiceField(
        queryset=Account.objects.all().get_dj_accounts())
    music_list = forms.MultipleChoiceField(
        choices=Music.choices, required=False)
    listeners = forms.MultipleChoiceField(
        choices=Booking.Listener.choices, required=False)

    class Meta:
        model = Booking
        fields = '__all__'

    def clean_music_list(self):
        return list(map(int, self.cleaned_data['music_list']))

    def clean_listeners(self):
        return list(map(int, self.cleaned_data['listeners']))


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    form = BookingCreationFormAdmin
    list_display = (
        'pk',
        'account_booker',
        'account_dj',
        'can_be_rated',
        'comment',
        'location_state',
        'location_city',
        'event_address',
        'date',
        'time',
        'dj_busy_dates',
        'status_info',
        'duration',
        'adjustment_minutes',
        'payment_intent_id',
        '_get_price_not_adjusted',
        'price',
        'applied_discounts',
        'sum_to_pay_from_card',
        'sum_to_pay_from_balance',
        'booker_fee',
        'dj_fee',
        'created_at'
    )

    def price(self, obj):
        return obj.get_price()

    def status_info(self, obj):
        return format_html(f'{obj.status}&nbsp;-&nbsp;{obj.get_status_display()}')

    def save_model(self, request, obj, form, change):
        obj.is_by_staff = True
        obj.change_author = request.user
        super().save_model(request, obj, form, change)


@admin.register(BookingChangeRecord)
class BookingChangeRecordAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'booking',
        'field_name',
        'value_before',
        'value_after',
        'is_by_staff',
        'created_at',
    )
    list_filter = ('value_before', 'value_after',)
    date_hierarchy = 'created_at'
    search_fields = ('booking__pk', 'field_name',)

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Transaction)
class TransactionAdmin(ModelAdminTotals):

    class Meta:
        model = Transaction

    search_fields = ('entity_pk', 'user__email', 'user__first_name', 'user__last_name',)

    list_display = (
        'pk',
        'user',
        'purpose',
        'amount',
        'entity',
        'entity_pk',
        'is_hold',
        'created_at'
    )

    list_totals = [('amount', Sum)]

    fields = [
        'pk',
        'user',
        'purpose',
        'amount',
        'entity',
        'entity_pk',
        'is_hold',
        'created_at'
    ]

    readonly_fields = [
        'pk',
        'user',
        'purpose',
        'amount',
        'entity',
        'entity_pk',
        'created_at'
    ]

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):

    class Meta:
        model = Withdrawal

    list_display = (
        'pk',
        'user',
        'amount',
        'status_info',
        'destination_card',
        'result',
        'created_at'
    )

    def status_info(self, obj):
        return format_html(f'{obj.status}&nbsp;-&nbsp;{obj.get_status_display()}')


@admin.register(BookingReview)
class BookingReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'booking', 'rating', 'is_by_booker', 'comment')
    list_filter = ('booking',)


@admin.register(Promocode)
class PromocodeAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'code',
        'promocode_type',
        'amount',
        'bookings_count',
        'max_application_count',
        'start_date',
        'end_date',
        'is_active',
        'updated_at',
        'created_at',
    )
    list_filter = ('created_at', 'is_active',)
    date_hierarchy = 'created_at'
    search_fields = ('code',)
    readonly_fields=('bookings', )

    def bookings_count(self, obj):
        return format_html(f'{len(obj.bookings.all())}')
