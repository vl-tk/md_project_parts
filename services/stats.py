import datetime
from collections import Counter
from datetime import date

from booking.models import Booking, BookingReview, Transaction
from dateutil.relativedelta import relativedelta
from dispute.models import Dispute
from django.conf import settings
from django.db.models import Count, F, Func, Q
from django.db.models.aggregates import Avg, Count, Max, Min
from django.utils import timezone
from users.enums import Music
from users.models import Account, BookerProfile, DJProfile
from utils.aggregates import Median


class StatsProvider:
    """
    Provider has many 'get_[some_calculated_parameter]' methods
    and one 'calculate_all' method to get all their results in a single dict
    """

    def calculate_all(self):
        calc_methods = [f for f in dir(self) if callable(getattr(self, f)) and f.startswith('get_')]
        return {method_name[4:]: getattr(self, method_name)() for method_name in calc_methods}

    def _calculate_bookings_value(self, bookings):
        return sum([b.get_price() * 100 for b in bookings])

    def _calculate_performers_earnings_from_bookings(self, bookings):
        return sum([(b.get_price() - b.dj_fee - b.booker_fee) * 100 for b in bookings])

    @property
    def lifetime_months(self) -> int:
        """
        Number of months since service lifetime started

        Used in providers where report date is specified
        """
        if not hasattr(self, 'end_date'):
            return None
        delta = relativedelta(self.end_date, settings.SERVICE_LIFETIME_START_DATETIME)
        return abs(delta.years) * 12 + abs(delta.months)

    @property
    def months_in_period(self) -> int:
        """
        Number of months between start_date and end_date

        Used in providers where report date is specified
        """
        if not hasattr(self, 'start_date') or not hasattr(self, 'end_date'):
            return None
        delta = relativedelta(self.end_date, self.start_date)
        return abs(delta.years) * 12 + abs(delta.months)


class StatsGeneralProvider(StatsProvider):

    def get_average_star_rating_for_booker(self) -> float:
        res = BookingReview.objects.filter(
            is_by_booker=False
        ).aggregate(
            Avg('rating')
        )
        return res['rating__avg']

    def get_average_star_rating_for_performer(self) -> float:
        res = BookingReview.objects.filter(
            is_by_booker=True
        ).aggregate(
            Avg('rating')
        )
        return res['rating__avg']

    def get_total_credits_in_bookers_accounts(self) -> float:
        accounts = Account.objects.all().get_valid_for_statistics()\
            .filter(booker_profile__isnull=False) \
            .select_related('user')

        total_sum: float = 0
        # TODO: possibly optimize
        for account in accounts:
            total_sum += Transaction.objects.get_user_balance(account.user)
        return total_sum

    def get_total_credits_in_performers_accounts(self) -> float:
        accounts = Account.objects.all().get_valid_for_statistics()\
            .filter(dj_profile__isnull=False) \
            .select_related('user')

        total_sum: float = 0
        for account in accounts:
            total_sum += Transaction.objects.get_user_balance(account.user)
        return total_sum

    def get_average_credits_in_bookers_accounts(self) -> float:
        accounts = Account.objects.all().get_valid_for_statistics()\
            .filter(booker_profile__isnull=False) \
            .select_related('user')

        total_sum: float = 0
        for account in accounts:
            total_sum += Transaction.objects.get_user_balance(
                account.user)

        return round(total_sum / len(accounts), 2)

    def get_average_credits_in_performers_accounts(self) -> float:
        accounts = Account.objects.all().get_valid_for_statistics()\
            .filter(dj_profile__isnull=False) \
            .select_related('user')

        total_sum: float = 0
        for account in accounts:
            total_sum += Transaction.objects.get_user_balance(
                account.user)

        return round(total_sum / len(accounts), 2)

    def get_number_of_bookers_verified(self) -> int:
        return Account.objects.all().get_valid_for_statistics() \
            .filter(booker_profile__isnull=False) \
            .select_related('user') \
            .count()

    def get_number_of_performers_verified(self) -> int:
        return Account.objects.all().get_valid_for_statistics() \
            .filter(dj_profile__isnull=False) \
            .select_related('user') \
            .count()

    def get_number_of_performers_able_to_play_clean_mix(self) -> int:
        return Account.objects.all().get_valid_for_statistics() \
            .filter(dj_profile__isnull=False) \
            .filter(dj_profile__clean_mix=True) \
            .select_related('user', 'dj_profile') \
            .count()

    def get_number_of_performers_able_to_play_virtual_mix(self) -> int:
        return Account.objects.all().get_valid_for_statistics() \
            .filter(dj_profile__isnull=False) \
            .filter(dj_profile__virtual_mix=True) \
            .select_related('user', 'dj_profile') \
            .count()

    def get_percentage_of_performers_able_to_play_clean_mix(self) -> int:
        num = self.get_number_of_performers_able_to_play_clean_mix()
        total = self.get_number_of_performers_verified()
        if total:
            return int(num / total * 100)

    def get_percentage_of_performers_able_to_play_virtual_mix(self) -> int:
        num = self.get_number_of_performers_able_to_play_virtual_mix()
        total = self.get_number_of_performers_verified()
        if total:
            return int(num / total * 100)


class StatsKPIProvider(StatsProvider):

    def get_top_reservation_locations(self):
        return list(Booking.objects.values('location_city', 'location_state') \
            .annotate(count=Count('id')).order_by('-count')[0:10])

    def get_event_types(self):
        results = list(Booking.objects.values('gig_type') \
            .annotate(count=Count('id')).order_by('-count')[0:5])
        for result in results:
            result['gig_type'] = [t for t in Booking.TYPES if result['gig_type'] == t[0]][0][1]
        return results

    def get_booker_music_preferences(self):
        values = BookerProfile.objects.annotate(
            arr_elements=Func(F('favorite_music'), function='unnest')
        ).values_list('arr_elements', flat=True)
        results = []
        for (value, count) in Counter(values).most_common(5):
            name = [m for m in Music.choices if m[0] == value][0][1]
            results.append({'music': name, 'count': count})
        return results

    def get_performer_music_preferences(self):
        values = DJProfile.objects.annotate(
             arr_elements=Func(F('music_can_play'), function='unnest')
        ).values_list('arr_elements', flat=True)
        results = []
        for (value, count) in Counter(values).most_common(5):
            name = [m for m in Music.choices if m[0] == value][0][1]
            results.append({'music': name, 'count': count})
        return results


class StatsServiceTimeProvider(StatsProvider):

    def get_average_number_of_hours_per_reservation(self) -> float:
        value = Booking.objects.aggregate(Avg('duration'))
        if value['duration__avg'] is not None:
            return round(value['duration__avg'] / 60, 1)

    def get_median_number_of_hours_for_reservation(self) -> float:
        value = Booking.objects.aggregate(Median('duration'))
        if value['duration__median'] is not None:
            return round(value['duration__median'] / 60, 1)

    def get_minimum_number_of_hours_for_reservation(self) -> float:
        value = Booking.objects.aggregate(Min('duration'))
        if value['duration__min'] is not None:
            return round(value['duration__min'] / 60, 1)

    def get_maximum_number_of_hours_for_reservation(self) -> float:
        value = Booking.objects.aggregate(Max('duration'))
        if value['duration__max'] is not None:
            return round(value['duration__max'] / 60, 1)


class StatsServiceEscrowProvider(StatsProvider):

    def get_number_of_gigs_in_escrow(self) -> int:
        return Booking.objects.get_accepted_by_dj().count()

    def get_value_of_gigs_in_escrow(self) -> float:
        return self._calculate_bookings_value(
            Booking.objects.get_accepted_by_dj()
        )

    def get_number_of_gigs_pending_acceptance(self) -> int:
        return Booking.objects.get_paid().count()

    def get_value_of_gigs_pending_acceptance(self) -> float:
        return self._calculate_bookings_value(
            Booking.objects.get_paid()
        )


class StatsServiceGigsProvider(StatsProvider):

    def __init__(self, start_date: datetime.date = None, end_date: datetime.date = None):

        if start_date is None:
            self.start_date = settings.SERVICE_LIFETIME_START_DATETIME
        else:
            self.start_date = start_date

        if end_date is None:
            self.end_date = timezone.now().date()
        else:
            self.end_date = end_date

    def _get_completed_bookings_from_date(self, start_date):
        return Booking.objects.get_completed() \
        .filter_before_and_including_date(date=self.end_date) \
        .filter(date__gte=start_date)

    # depend on 2 dates

    def get_period_percentage_of_gigs_using_a_clean_mix(self) -> int:
        num = Booking.objects.filter(clean_mix=True) \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .count()
        total = Booking.objects.all() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .count()
        if total:
            return int(num / total * 100)

    def get_period_percentage_of_gigs_played_virtually(self) -> int:
        num = Booking.objects.filter(virtual_mix=True) \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .count()
        total = Booking.objects.all() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .count()
        if total:
            return int(num / total * 100)

    def get_period_reservations_completed(self) -> int:
        bookings = Booking.objects.get_completed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date)
        return bookings.count()

    def get_period_reservations_value(self) -> int:
        bookings = Booking.objects.get_completed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date)
        return self._calculate_bookings_value(bookings)

    def get_period_monthly_completed_average(self) -> float:
        if self.months_in_period:
            return round(
                self.get_period_reservations_completed() / self.months_in_period,
                2
            )

    def get_period_monthly_reservation_value_average(self) -> float:
        if self.months_in_period:
            return round(
                self.get_period_reservations_value() / self.months_in_period,
                2
            )

    # have FIXED start_date - till the end date (NOW by default)

    def get_lifetime_reservations_completed(self) -> int:
        bookings = Booking.objects.get_completed() \
        .filter_before_and_including_date(date=self.end_date)
        return bookings.count()

    def get_lifetime_reservations_value(self) -> int:
        bookings = Booking.objects.get_completed() \
        .filter_before_and_including_date(date=self.end_date)
        return self._calculate_bookings_value(bookings)

    def get_lifetime_monthly_completed_average(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_reservations_completed() / self.lifetime_months,
                2
            )

    def get_lifetime_monthly_reservation_value_average(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_reservations_value() / self.lifetime_months,
                2
            )

    def get_ytd_reservations_completed(self) -> int:
        return self._get_completed_bookings_from_date(
            start_date=date(self.end_date.year, 1, 1)
        ).count()

    def get_ytd_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_completed_bookings_from_date(
                start_date=date(self.end_date.year, 1, 1)
            )
        )

    def get_last_6_months_reservations_completed(self) -> int:
        return self._get_completed_bookings_from_date(
            start_date=self.end_date + relativedelta(months=-6)
        ).count()

    def get_last_6_months_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_completed_bookings_from_date(
                start_date=self.end_date + relativedelta(months=-6)
            )
        )

    def get_current_month_reservations_completed(self) -> int:
        return self._get_completed_bookings_from_date(
            start_date=date(self.end_date.year, self.end_date.month, 1)
        ).count()

    def get_current_month_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_completed_bookings_from_date(
                start_date=date(self.end_date.year, self.end_date.month, 1)
            )
        )


class StatsServiceCancelationsProvider(StatsProvider):

    def __init__(self, start_date: datetime.date = None, end_date: datetime.date = None):

        if start_date is None:
            self.start_date = settings.SERVICE_LIFETIME_START_DATETIME
        else:
            self.start_date = start_date

        if end_date is None:
            self.end_date = timezone.now().date()
        else:
            self.end_date = end_date

    def _get_canceled_bookings_from_date(self, start_date):
        return Booking.objects.get_canceled() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=start_date)

    # depend on 2 dates

    def get_period_percentage_of_reservations_canceled(self) -> int:
        num = Booking.objects.all() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(
                status__in=[Booking.Status.CANCELED_BY_BOOKER, Booking.Status.CANCELED_BY_DJ]
            ).count()
        total = Booking.objects.all() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .count()
        if total:
            return int(num / total * 100)

    def get_period_percentage_of_reservations_canceled_by_booker(self) -> int:
        num = Booking.objects.all() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(
                status__in=[Booking.Status.CANCELED_BY_BOOKER]
            ).count()
        total = Booking.objects.all() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .count()
        if total:
            return int(num / total * 100)

    def get_period_percentage_of_reservations_canceled_by_performer(self) -> int:
        num = Booking.objects.all() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(
                status__in=[Booking.Status.CANCELED_BY_DJ]
            ).count()
        total = Booking.objects.all() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .count()
        if total:
            return int(num / total * 100)

    def get_period_reservations_canceled(self) -> int:
        bookings = Booking.objects.get_canceled() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date)
        return bookings.count()

    def get_period_canceled_reservations_value(self) -> int:
        bookings = Booking.objects.get_canceled() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date)
        return self._calculate_bookings_value(bookings)

    def get_period_monthly_reservation_cancelation_average(self) -> float:
        if self.months_in_period:
            return round(
                self.get_period_reservations_canceled() / self.months_in_period,
                2
            )

    def get_period_monthly_reservation_cancelation_value_average(self) -> float:
        if self.months_in_period:
            return round(
                self.get_period_canceled_reservations_value() / self.months_in_period,
                2
            )

    # have FIXED start_date - till the end date (NOW by default)

    def get_lifetime_reservations_canceled(self) -> int:
        bookings = Booking.objects.get_canceled() \
            .filter_before_and_including_date(date=self.end_date)
        return bookings.count()

    def get_lifetime_canceled_reservations_value(self) -> int:
        bookings = Booking.objects.get_canceled() \
            .filter_before_and_including_date(date=self.end_date)
        return self._calculate_bookings_value(bookings)

    def get_lifetime_monthly_reservation_cancelation_average(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_reservations_canceled() / self.lifetime_months,
                2
            )

    def get_lifetime_monthly_reservation_cancelation_value_average(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_canceled_reservations_value() / self.lifetime_months,
                2
            )

    def get_ytd_reservations_canceled(self) -> int:
        return self._get_canceled_bookings_from_date(
            start_date=date(self.end_date.year, 1, 1)
        ).count()

    def get_ytd_canceled_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_canceled_bookings_from_date(
                start_date=date(self.end_date.year, 1, 1)
            )
        )

    def get_last_6_months_reservations_canceled(self) -> int:
        return self._get_canceled_bookings_from_date(
            start_date=self.end_date + relativedelta(months=-6)
        ).count()

    def get_last_6_months_canceled_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_canceled_bookings_from_date(
                start_date=self.end_date + relativedelta(months=-6)
            )
        )

    def get_current_month_reservations_canceled(self) -> int:
        return self._get_canceled_bookings_from_date(
            start_date=date(self.end_date.year, self.end_date.month, 1)
        ).count()

    def get_current_month_canceled_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_canceled_bookings_from_date(
                start_date=date(self.end_date.year, self.end_date.month, 1)
            )
        )


class StatsServiceDisputesProvider(StatsProvider):

    def __init__(self, start_date: datetime.date = None, end_date: datetime.date = None):

        if start_date is None:
            self.start_date = settings.SERVICE_LIFETIME_START_DATETIME
        else:
            self.start_date = start_date

        if end_date is None:
            self.end_date = timezone.now().date()
        else:
            self.end_date = end_date

    # depend on 2 dates

    def get_period_percentage_of_reservations_disputed(self) -> int:
        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .count()
        total = Booking.objects.all() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .count()
        if total:
            return int(num / total * 100)

    def get_period_average_gig_value_of_reservation_disputed(self) -> float:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date)
        if bookings:
            return round(
                self._calculate_bookings_value(bookings) / bookings.count(),
                2
            )

    # common

    def _get_bookings_disputed_from_date(self, start_date):
        return Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=start_date)


class StatsServiceAllDisputesProvider(StatsServiceDisputesProvider):

    def get_period_reservations_disputed(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date)
        return bookings.count()

    def get_period_disputed_reservations_value(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date)
        return self._calculate_bookings_value(bookings)

    # awarded to booker and performer

    def get_period_percentage_of_disputes_awarded_to_booker(self) -> int:
        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR, \
                Dispute.Status.DJ_CONCEDED
            ]).count()
        total = self.get_period_reservations_disputed()
        if total:
            return int(num / total * 100)

    def get_period_value_of_disputes_awarded_to_booker(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR, \
                Dispute.Status.DJ_CONCEDED
            ])
        return self._calculate_bookings_value(bookings)

    def get_period_average_value_of_disputes_awarded_to_booker(self) -> float:
        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR,
                Dispute.Status.DJ_CONCEDED
            ]).count()
        if num:
            return round(
                self.get_period_value_of_disputes_awarded_to_booker() / num,
                2
            )

    def get_period_percentage_of_disputes_awarded_to_performer(self) -> int:
        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ]).count()
        total = self.get_period_reservations_disputed()
        if total:
            return int(num / total * 100)

    def get_period_value_of_disputes_awarded_to_performer(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ])
        return self._calculate_bookings_value(bookings)

    def get_period_average_value_of_disputes_awarded_to_performer(self) -> float:

        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ]).count()

        if num:
            return round(
                self.get_period_value_of_disputes_awarded_to_performer() / num,
                2
            )

    # have FIXED start_date - till the end date (NOW by default)

    def get_lifetime_reservations_disputed(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date)
        return bookings.count()

    def get_lifetime_disputed_reservations_value(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date)
        return self._calculate_bookings_value(bookings)

    def get_lifetime_monthly_dispute_average(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_reservations_disputed() / self.lifetime_months,
                2
            )

    def get_lifetime_monthly_dispute_average_value(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_disputed_reservations_value() / self.lifetime_months,
                2
            )

    def get_ytd_reservations_disputed(self) -> int:
        return self._get_bookings_disputed_from_date(
            start_date=date(self.end_date.year, 1, 1)
        ).count()

    def get_ytd_disputed_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_bookings_disputed_from_date(
                start_date=date(self.end_date.year, 1, 1)
            )
        )

    def get_last_6_months_reservations_disputed(self) -> int:
        return self._get_bookings_disputed_from_date(
            start_date=self.end_date + relativedelta(months=-6)
        ).count()

    def get_last_6_months_disputed_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_bookings_disputed_from_date(
                start_date=self.end_date + relativedelta(months=-6)
            )
        )

    def get_current_month_reservations_disputed(self) -> int:
        return self._get_bookings_disputed_from_date(
            start_date=date(self.end_date.year, self.end_date.month, 1)
        ).count()

    def get_current_month_disputed_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_bookings_disputed_from_date(
                start_date=date(self.end_date.year, self.end_date.month, 1)
            )
        )


class StatsServiceDisputesWithoutMDDProvider(StatsServiceDisputesProvider):

    def _get_bookings_disputed_from_date_without_mdd(self, start_date):
        return Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.DJ_CONCEDED
            ])

    # depend on 2 dates

    def get_period_reservations_disputed(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.DJ_CONCEDED
            ])
        return bookings.count()

    def get_period_disputed_reservations_value(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.DJ_CONCEDED
            ])
        return self._calculate_bookings_value(bookings)

    def get_period_percentage_of_disputes_awarded_to_booker(self) -> int:
        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.DJ_CONCEDED
            ]).count()
        total = self.get_period_reservations_disputed()
        if total:
            return int(num / total * 100)

    def get_period_value_of_disputes_awarded_to_booker(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.DJ_CONCEDED
            ])
        return self._calculate_bookings_value(bookings)

    def get_period_monthly_dispute_average_without_mdd(self) -> float:
        if self.months_in_period:
            return round(
                self.get_period_reservations_disputed() / self.months_in_period,
                2
            )

    def get_period_monthly_dispute_average_value_without_mdd(self) -> float:
        if self.months_in_period:
            return round(
                self.get_period_disputed_reservations_value() / self.months_in_period,
                2
            )

    def get_period_average_value_of_disputes_awarded_to_booker(self) -> float:
        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.DJ_CONCEDED
            ]).count()
        if num:
            return round(
                self.get_period_value_of_disputes_awarded_to_booker() / num,
                2
            )

    # important:  by design there are no disputes conceded to performer

    # have FIXED start_date - till the end date (NOW by default)

    def get_lifetime_reservations_disputed(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(dispute__status__in=[
                Dispute.Status.DJ_CONCEDED
            ])
        return bookings.count()

    def get_lifetime_disputed_reservations_value(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(dispute__status__in=[
                Dispute.Status.DJ_CONCEDED
            ])
        return self._calculate_bookings_value(bookings)

    # all for without mdd

    def get_lifetime_monthly_dispute_average_without_mdd(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_reservations_disputed() / self.lifetime_months,
                2
            )

    def get_lifetime_monthly_dispute_average_value_without_mdd(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_disputed_reservations_value() / self.lifetime_months,
                2
            )

    def get_ytd_reservations_disputed(self) -> int:
        return self._get_bookings_disputed_from_date_without_mdd(
            start_date=date(self.end_date.year, 1, 1)
        ) \
            .count()

    def get_ytd_disputed_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_bookings_disputed_from_date_without_mdd(
                start_date=date(self.end_date.year, 1, 1)
            )
        )

    def get_last_6_months_reservations_disputed(self) -> int:
        return self._get_bookings_disputed_from_date_without_mdd(
            start_date=self.end_date + relativedelta(months=-6)
        ).count()

    def get_last_6_months_disputed_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_bookings_disputed_from_date_without_mdd(
                start_date=self.end_date + relativedelta(months=-6)
            )
        )

    def get_current_month_reservations_disputed(self) -> int:
        return self._get_bookings_disputed_from_date_without_mdd(
            start_date=date(self.end_date.year, self.end_date.month, 1)
        ).count()

    def get_current_month_disputed_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_bookings_disputed_from_date_without_mdd(
                start_date=date(self.end_date.year, self.end_date.month, 1)
            )
        )


class StatsServiceDisputesByMDDProvider(StatsServiceDisputesProvider):

    def _get_bookings_disputed_from_date_by_mdd(self, start_date):
        return Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR,
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ])

    # depend on 2 dates

    def get_period_percentage_of_disputes_awarded_to_booker(self) -> int:
        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR
            ]).count()
        total = self.get_period_reservations_disputed()
        if total:
            return int(num / total * 100)

    def get_period_value_of_disputes_awarded_to_booker(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR
            ])
        return self._calculate_bookings_value(bookings)

    def get_period_average_value_of_disputes_awarded_to_booker(self) -> float:
        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR
            ]).count()
        if num:
            return round(
                self.get_period_value_of_disputes_awarded_to_booker() / num,
                2
            )

    def get_period_percentage_of_disputes_awarded_to_performer(self) -> int:
        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ]).count()
        total = self.get_lifetime_reservations_disputed()
        if total:
            return int(num / total * 100)

    def get_period_value_of_disputes_awarded_to_performer(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ])
        return self._calculate_bookings_value(bookings)

    def get_period_average_value_of_disputes_awarded_to_performer(self) -> float:

        num = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ]).count()

        if num:
            return round(
                self.get_period_value_of_disputes_awarded_to_performer() / num,
                2
            )

    def get_period_reservations_disputed(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR,
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ])
        return bookings.count()

    def get_period_disputed_reservations_value(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(date__gte=self.start_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR,
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ])
        return self._calculate_bookings_value(bookings)

    def get_period_monthly_dispute_average_by_mdd(self) -> float:
        if self.months_in_period:
            return round(
                self.get_period_reservations_disputed() / self.months_in_period,
                2
            )

    # have FIXED start_date - till the end date (NOW by default)

    def get_lifetime_monthly_dispute_average_value_by_mdd(self) -> float:
        if self.months_in_period:
            return round(
                self.get_lifetime_disputed_reservations_value() / self.months_in_period,
                2
            )

    def get_lifetime_reservations_disputed(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR,
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ])
        return bookings.count()

    def get_lifetime_disputed_reservations_value(self) -> int:
        bookings = Booking.objects.get_disputed() \
            .filter_before_and_including_date(date=self.end_date) \
            .filter(dispute__status__in=[
                Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR,
                Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
            ])
        return self._calculate_bookings_value(bookings)

    def get_lifetime_monthly_dispute_average_by_mdd(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_reservations_disputed() / self.lifetime_months,
                2
            )

    def get_lifetime_monthly_dispute_average_value_by_mdd(self) -> float:
        if self.lifetime_months:
            return round(
                self.get_lifetime_disputed_reservations_value() / self.lifetime_months,
                2
            )

    def get_ytd_reservations_disputed(self) -> int:
        return self._get_bookings_disputed_from_date_by_mdd(
            start_date=date(self.end_date.year, 1, 1)
        ) \
            .count()

    def get_ytd_disputed_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_bookings_disputed_from_date_by_mdd(
                start_date=date(self.end_date.year, 1, 1)
            )
        )

    def get_last_6_months_reservations_disputed(self) -> int:
        return self._get_bookings_disputed_from_date_by_mdd(
            start_date=self.end_date + relativedelta(months=-6)
        ).count()

    def get_last_6_months_disputed_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_bookings_disputed_from_date_by_mdd(
                start_date=self.end_date + relativedelta(months=-6)
            )
        )

    def get_current_month_reservations_disputed(self) -> int:
        return self._get_bookings_disputed_from_date_by_mdd(
            start_date=date(self.end_date.year, self.end_date.month, 1)
        ).count()

    def get_current_month_disputed_reservations_value(self) -> int:
        return self._calculate_bookings_value(
            self._get_bookings_disputed_from_date_by_mdd(
                start_date=date(self.end_date.year, self.end_date.month, 1)
            )
        )


class StatsBookerCreditsProvider(StatsProvider):

    def __init__(self, account):
        self.account = account
        self.date = timezone.now().date()

    def _get_bookings_paid_by_booker(self):

        completed_bookings = Booking.objects.filter(
            status=Booking.Status.COMPLETED
        ).by_booker(account=self.account)

        disputed_in_bookers_favor_bookings = Booking.objects.filter(
            status=Booking.Status.DISPUTED
        ) \
        .filter(dispute__status__in=[
            Dispute.Status.RESOLVED_BY_STAFF_TO_BOOKER_FAVOR, \
            Dispute.Status.DJ_CONCEDED
        ]) \
        .by_booker(account=self.account)

        return completed_bookings | disputed_in_bookers_favor_bookings

    def get_credits_in_the_account(self):
        return Transaction.objects.get_user_balance(
            self.account.user)

    def get_amount_in_escrow(self):
        return self._calculate_bookings_value(
            Booking.objects.get_accepted_by_dj().by_booker(
                account=self.account
            )
        )

    def get_lifetime_paid_for_bookings(self):
        return self._calculate_bookings_value(
            self._get_bookings_paid_by_booker()
        )

    def get_ytd_paid_for_bookings(self):
        bookings = self._get_bookings_paid_by_booker().filter(
            date__gte=date(self.date.year, 1, 1)
        )
        return self._calculate_bookings_value(bookings)

    def get_amount_in_pending_bookings(self):
        return self._calculate_bookings_value(
            Booking.objects.get_paid().by_booker(
                account=self.account
            )
        )


class StatsPerformerCreditsProvider(StatsBookerCreditsProvider):

    def __init__(self, account):
        self.account = account
        self.date = timezone.now().date()

    def get_amount_in_escrow(self):
        return self._calculate_bookings_value(
            Booking.objects.get_accepted_by_dj().by_dj(
                account=self.account
            )
        )

    def get_amount_in_pending_bookings(self):
        return self._calculate_bookings_value(
            Booking.objects.get_paid().by_dj(
                account=self.account
            )
        )

    def _get_bookings_earned_by_performer(self):

        completed_bookings = Booking.objects.filter(
            status=Booking.Status.COMPLETED
        ).by_dj(account=self.account)

        disputed_in_dj_favor_bookings = Booking.objects.filter(
            status=Booking.Status.DISPUTED
        ) \
        .filter(dispute__status__in=[
            Dispute.Status.RESOLVED_BY_STAFF_TO_DJ_FAVOR
        ]) \
        .by_dj(account=self.account)

        return completed_bookings | disputed_in_dj_favor_bookings

    def get_lifetime_earned_from_performances(self):
        return self._calculate_performers_earnings_from_bookings(
            self._get_bookings_earned_by_performer()
        )

    def get_ytd_earned_from_performances(self):
        bookings = self._get_bookings_earned_by_performer().filter(
            date__gte=date(self.date.year, 1, 1)
        )
        return self._calculate_performers_earnings_from_bookings(bookings)
