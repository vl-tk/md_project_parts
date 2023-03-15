from dateutil.relativedelta import relativedelta

import logging
logger = logging.getLogger('django')

from django.conf import settings
from django.utils import timezone
from django.utils.timezone import make_aware
from django.db import transaction
from django.db.models.aggregates import Avg, Count, Sum
from django.db.models import F

from booking.models import Booking, BookingReview
from dispute.models import Dispute
from users.models import PastGig

from utils.loggers import current_func_name


class RatingCalculator:
    """
    Class containing common methods for every Rating calculator
    """

    STABILIZER = 35
    MAX_STARS_RATING = 5
    MONTHS_IN_YEAR = 12

    def calculate_percentage(self, rating: float, percentage: int) -> int:
        """
        Calculates share in Total Rating for user based on parameters:
        rating: float - one of the subratings for user (0...1)
        percentage: int - subrating percentage (5%,11%,14%,35%) in Total Rating
        """
        if rating >= 1:
            return percentage
        return int(round(rating * percentage))

    def get_today_start(self):
        return timezone.now().replace(
            hour=0, minute=0, second=0, microsecond=0)

    def get_months_since_date(self, date) -> int:
        delta = relativedelta(timezone.now(), date)
        return abs(delta.years) * self.MONTHS_IN_YEAR + abs(delta.months)

    @property
    def months_since_service_start(self) -> int:
        delta = relativedelta(
            timezone.now(),
            make_aware(settings.SERVICE_LIFETIME_START_DATETIME)
        )
        return abs(delta.years) * self.MONTHS_IN_YEAR + abs(delta.months)

    def get_rating_records_for_djs(self):
        from users.models import RatingRecord
        return RatingRecord.objects.filter(
            dj_profile__in=self.ratings.keys()).only('pk', 'dj_profile_id')

    def get_rating_records_for_bookers(self):
        from users.models import RatingRecord
        return RatingRecord.objects.filter(
            booker_profile__in=self.ratings.keys()).only('pk', 'booker_profile_id')

    def _bulk_update_records(self, objects, fields):
        from users.models import RatingRecord
        RatingRecord.objects.bulk_update(objects, fields, batch_size=100)

    def calculate_and_update_total_rating(self):

        from users.models import RatingRecord, BookerProfile, DJProfile

        with transaction.atomic():
            records_for_djs = RatingRecord.objects.filter(
                dj_profile__isnull=False
            ).select_related('dj_profile').select_for_update()
            to_update = []
            for r in records_for_djs:
                total_rating = self.STABILIZER + \
                    r.avg_booking_star_rating_percentage + \
                    r.signed_time_rating_percentage + \
                    r.number_of_booking_rating_percentage + \
                    r.number_of_disputes_rating_percentage
                dj_profile = r.dj_profile
                dj_profile.rating = total_rating
                to_update.append(dj_profile)
            DJProfile.objects.bulk_update(to_update, ['rating'], batch_size=100)

            records_for_bookers = RatingRecord.objects.filter(
                booker_profile__isnull=False
            ).select_related('booker_profile').select_for_update()
            to_update = []
            for r in records_for_bookers:
                total_rating = self.STABILIZER + \
                    r.avg_booking_star_rating_percentage + \
                    r.signed_time_rating_percentage + \
                    r.number_of_booking_rating_percentage + \
                    r.number_of_disputes_rating_percentage
                booker_profile = r.booker_profile
                booker_profile.rating = total_rating
                to_update.append(booker_profile)
            BookerProfile.objects.bulk_update(to_update, ['rating'], batch_size=100)

class PerformersStarsRatingCalculator(RatingCalculator):

    PERCENTAGE = 35

    def get_rating_for_performers(self):

        logger.debug(f"{__class__.__name__}.{current_func_name()}: for {self.get_today_start()}")

        # collects list of (dj_profile, rating_sum, rating_count)
        booking_ratings_values = BookingReview.objects.filter(
            is_by_booker=True,
            created_at__lte=self.get_today_start()
        ).values(
            'booking__account_dj__dj_profile'
        ).annotate(
            rating_sum=Sum('rating'),
            rating_count=Count('rating'),
            dj_profile=F('booking__account_dj__dj_profile')
        )

        booking_ratings = {p['dj_profile']: (p['rating_sum'], p['rating_count'],) for p in booking_ratings_values}
        logger.debug(f"booking_ratings: {booking_ratings}")

        # collects list of (dj_profile, rating_sum, rating_count for past gig)
        past_gig_ratings_values = PastGig.objects.filter(
            is_confirm=True,
            created_at__lte=self.get_today_start()
        ).values(
            'dj_profile'
        ).annotate(
            rating_sum=Sum('value'),
            rating_count=Count('value')
        )

        past_gig_ratings = {p['dj_profile']: (p['rating_sum'], p['rating_count'],) for p in past_gig_ratings_values}
        logger.debug(f"past_gig_ratings: {past_gig_ratings}")

        unique_dj_profiles_ids = set(list(booking_ratings.keys()) + list(past_gig_ratings.keys()))

        self.ratings = {}
        for dj_profile in unique_dj_profiles_ids:
            total_rating = booking_ratings.get(dj_profile, (0, 0))[0] + past_gig_ratings.get(dj_profile, (0, 0))[0]
            total_count = booking_ratings.get(dj_profile, (0, 0))[1] + past_gig_ratings.get(dj_profile, (0, 0))[1]
            avg_star_rating = round(total_rating / total_count, 1)
            self.ratings[dj_profile] = avg_star_rating

        logger.debug(f"ratings: {self.ratings}")

    def update_records(self):
        to_update = []
        for r in self.get_rating_records_for_djs():
            r.avg_booking_star_rating = self.ratings[r.dj_profile_id]
            r.avg_booking_star_rating_percentage = self.calculate_percentage(
                self.ratings[r.dj_profile_id] / self.MAX_STARS_RATING,
                self.PERCENTAGE
            )
            to_update.append(r)
        self._bulk_update_records(to_update, [
            'avg_booking_star_rating',
            'avg_booking_star_rating_percentage'
        ])


class BookersStarsRatingCalculator(RatingCalculator):

    PERCENTAGE = 35

    def get_rating_for_bookers(self):

        # collects list of (booker_profile, avg_rating)
        ratings = BookingReview.objects.filter(
            is_by_booker=False,
            created_at__lte=self.get_today_start()
        ).values_list(
            'booking__account_booker__booker_profile'
        ).annotate(
            Avg('rating')
        )
        self.ratings = {p[0]: p[1] for p in ratings}

        logger.debug(f"{__class__.__name__}.{current_func_name()}: for {self.get_today_start()}")
        logger.debug(f"ratings: {self.ratings}")

    def update_records(self):
        to_update = []
        for r in self.get_rating_records_for_bookers():
            r.avg_booking_star_rating = self.ratings[r.booker_profile_id]
            r.avg_booking_star_rating_percentage = self.calculate_percentage(
                self.ratings[r.booker_profile_id] / self.MAX_STARS_RATING,
                self.PERCENTAGE
            )
            to_update.append(r)
        self._bulk_update_records(to_update, [
            'avg_booking_star_rating',
            'avg_booking_star_rating_percentage'
        ])


class PerformersBookingNumberRatingCalculator(RatingCalculator):

    PERCENTAGE = 14

    def get_rating_for_performers(self):

        number_of_bookings_per_dj = Booking.objects.filter(
            created_at__lte=self.get_today_start()
        ).values_list(
            'account_dj__dj_profile'
        ).annotate(
            Count('pk')
        )

        total_number_of_bookings = Booking.objects.filter(
            created_at__lte=self.get_today_start()
        ).aggregate(bookings_count=Count('pk'))

        total_number_of_performers = Booking.objects.filter(
            created_at__lte=self.get_today_start()
        ).values_list(
            'account_dj__dj_profile'
        ).aggregate(
            dj_count=Count('account_dj__dj_profile', distinct=True)
        )

        self.ratings = {}
        if total_number_of_performers['dj_count'] > 0:

            avg_number_of_bookings = total_number_of_bookings['bookings_count'] / \
                total_number_of_performers['dj_count']

            for p in number_of_bookings_per_dj:
                self.ratings[p[0]] = p[1] / avg_number_of_bookings

        logger.debug(f"{__class__.__name__}.{current_func_name()}: for {self.get_today_start()}")
        logger.debug(f"number_of_bookings_per_dj: {number_of_bookings_per_dj}")
        logger.debug(f"total_number_of_bookings: {total_number_of_bookings}")
        logger.debug(f"total_number_of_performers: {total_number_of_performers}")
        logger.debug(f"ratings: {self.ratings}")

    def update_records(self):
        if not self.ratings:
            return
        to_update = []
        for r in self.get_rating_records_for_djs():
            r.number_of_booking_rating = self.ratings[r.dj_profile_id]
            r.number_of_booking_rating_percentage = self.calculate_percentage(
                self.ratings[r.dj_profile_id],
                self.PERCENTAGE
            )
            to_update.append(r)
        self._bulk_update_records(to_update, [
            'number_of_booking_rating',
            'number_of_booking_rating_percentage'
        ])


class BookersBookingNumberRatingCalculator(RatingCalculator):

    PERCENTAGE = 14

    def get_rating_for_bookers(self):

        number_of_bookings_per_booker = Booking.objects.filter(
            created_at__lte=self.get_today_start()
        ).values_list(
            'account_booker__booker_profile'
        ).annotate(
            Count('pk')
        )

        total_number_of_bookings = Booking.objects.filter(
            created_at__lte=self.get_today_start()
        ).aggregate(bookings_count=Count('pk'))

        total_number_of_bookers = Booking.objects.filter(
            created_at__lte=self.get_today_start()
        ).values_list(
            'account_booker__booker_profile'
        ).aggregate(
            booker_count=Count('account_booker__booker_profile', distinct=True)
        )

        self.ratings = {}
        if total_number_of_bookers['booker_count'] > 0:

            avg_number_of_bookings = total_number_of_bookings['bookings_count'] / \
                total_number_of_bookers['booker_count']

            for p in number_of_bookings_per_booker:
                self.ratings[p[0]] = p[1] / avg_number_of_bookings

        logger.debug(f"{__class__.__name__}.{current_func_name()}: for {self.get_today_start()}")
        logger.debug(
            f"number_of_bookings_per_booker: {number_of_bookings_per_booker}")
        logger.debug(f"total_number_of_bookings: {total_number_of_bookings}")
        logger.debug(f"total_number_of_bookers: {total_number_of_bookers}")
        logger.debug(f"ratings: {self.ratings}")

    def update_records(self):
        to_update = []
        for r in self.get_rating_records_for_bookers():
            r.number_of_booking_rating = self.ratings[r.booker_profile_id]
            r.number_of_booking_rating_percentage = self.calculate_percentage(
                self.ratings[r.booker_profile_id],
                self.PERCENTAGE
            )
            to_update.append(r)
        self._bulk_update_records(to_update, [
            'number_of_booking_rating',
            'number_of_booking_rating_percentage'
        ])


class PerformersSignedMonthsRatingCalculator(RatingCalculator):

    PERCENTAGE = 5

    def get_rating_for_performers(self):

        from users.models import DJProfile

        activation_dates = DJProfile.objects.filter(
            user__is_email_active=True,
            user__email_activation_date__lte=self.get_today_start()
        ).values_list(
            'pk', 'user__email_activation_date'
        )

        self.ratings = {}
        for p in activation_dates:
            self.ratings[p[0]] = self.get_months_since_date(
                p[1]) / self.months_since_service_start

        logger.debug(f"{__class__.__name__}.{current_func_name()}: for {self.get_today_start()}")
        logger.debug(f"activation_dates: {activation_dates}")
        logger.debug(f"ratings: {self.ratings}")

    def update_records(self):
        to_update = []
        for r in self.get_rating_records_for_djs():
            r.signed_time_rating = self.ratings[r.dj_profile_id]
            r.signed_time_rating_percentage = self.calculate_percentage(
                self.ratings[r.dj_profile_id],
                self.PERCENTAGE
            )
            to_update.append(r)
        self._bulk_update_records(to_update, [
            'signed_time_rating',
            'signed_time_rating_percentage'
        ])


class BookersSignedMonthsRatingCalculator(RatingCalculator):

    PERCENTAGE = 5

    def get_rating_for_bookers(self):

        from users.models import BookerProfile

        activation_dates = BookerProfile.objects.filter(
            user__is_email_active=True,
            user__email_activation_date__lte=self.get_today_start()
        ).values_list(
            'pk', 'user__email_activation_date'
        )

        self.ratings = {}
        for p in activation_dates:
            self.ratings[p[0]] = self.get_months_since_date(
                p[1]) / self.months_since_service_start

        logger.debug(f"{__class__.__name__}.{current_func_name()}: for {self.get_today_start()}")
        logger.debug(f"activation_dates: {activation_dates}")
        logger.debug(f"ratings: {self.ratings}")

    def update_records(self):
        to_update = []
        for r in self.get_rating_records_for_bookers():
            r.signed_time_rating = self.ratings[r.booker_profile_id]
            r.signed_time_rating_percentage = self.calculate_percentage(
                self.ratings[r.booker_profile_id],
                self.PERCENTAGE
            )
            to_update.append(r)
        self._bulk_update_records(to_update, [
            'signed_time_rating',
            'signed_time_rating_percentage'
        ])


class PerformersDisputesRatingCalculator(RatingCalculator):

    PERCENTAGE = 11

    def get_rating_for_performers(self):

        number_of_bookings_per_dj = Booking.objects.filter(
            created_at__lte=self.get_today_start()
        ).values_list(
            'account_dj__dj_profile'
        ).annotate(
            bookings_count=Count('pk')
        ).filter(
            bookings_count__gt=0
        )

        number_of_disputes_per_dj = Dispute.objects.filter(
            created_at__lte=self.get_today_start()
        ).select_related(
            'booking__account_dj'
        ).values_list(
            'booking__account_dj__dj_profile'
        ).annotate(
            Count('pk')
        )

        bookings_numbers = {p[0]:p[1] for p in number_of_bookings_per_dj}
        disputes_numbers = {p[0]:p[1] for p in number_of_disputes_per_dj}

        self.ratings = {}
        for dj_profile_id, bookings_num in bookings_numbers.items():
            self.ratings[dj_profile_id] = disputes_numbers.get(dj_profile_id, 0) / bookings_num

        logger.debug(f"{__class__.__name__}.{current_func_name()}: for {self.get_today_start()}")
        logger.debug(f"number_of_bookings_per_dj: {number_of_bookings_per_dj}")
        logger.debug(f"number_of_disputes_per_dj: {number_of_disputes_per_dj}")
        logger.debug(f"bookings_numbers: {bookings_numbers}")
        logger.debug(f"disputes_numbers: {disputes_numbers}")
        logger.debug(f"ratings: {self.ratings}")

    def update_records(self):
        to_update = []
        for r in self.get_rating_records_for_djs():
            r.number_of_disputes_rating = self.ratings[r.dj_profile_id]
            r.number_of_disputes_rating_percentage = self.PERCENTAGE - self.calculate_percentage(
                self.ratings[r.dj_profile_id],
                self.PERCENTAGE
            )
            to_update.append(r)
        self._bulk_update_records(to_update, [
            'number_of_disputes_rating',
            'number_of_disputes_rating_percentage'
        ])



class BookersDisputesRatingCalculator(RatingCalculator):

    PERCENTAGE = 11

    def get_rating_for_bookers(self):

        number_of_bookings_per_booker = Booking.objects.filter(
            created_at__lte=self.get_today_start()
        ).values_list(
            'account_booker__booker_profile'
        ).annotate(
            bookings_count=Count('pk')
        ).filter(
            bookings_count__gt=0
        )

        number_of_disputes_per_booker = Dispute.objects.filter(
            created_at__lte=self.get_today_start()
        ).values_list(
            'booking__account_booker__booker_profile'
        ).annotate(
            Count('pk')
        )

        bookings_numbers = {p[0]: p[1] for p in number_of_bookings_per_booker}
        disputes_numbers = {p[0]: p[1] for p in number_of_disputes_per_booker}

        self.ratings = {}
        for booker_profile_id, bookings_num in bookings_numbers.items():
            self.ratings[booker_profile_id] = disputes_numbers.get(booker_profile_id, 0) / bookings_num

        logger.debug(f"{__class__.__name__}.{current_func_name()}: for {self.get_today_start()}")
        logger.debug(f"number_of_bookings_per_booker: {number_of_bookings_per_booker}")
        logger.debug(f"number_of_disputes_per_booker: {number_of_disputes_per_booker}")
        logger.debug(f"bookings_numbers: {bookings_numbers}")
        logger.debug(f"disputes_numbers: {disputes_numbers}")
        logger.debug(f"ratings: {self.ratings}")

    def update_records(self):
        to_update = []
        for r in self.get_rating_records_for_bookers():
            r.number_of_disputes_rating = self.ratings[r.booker_profile_id]
            r.number_of_disputes_rating_percentage = self.PERCENTAGE - self.calculate_percentage(
                self.ratings[r.booker_profile_id],
                self.PERCENTAGE
            )
            to_update.append(r)
        self._bulk_update_records(to_update, [
            'number_of_disputes_rating',
            'number_of_disputes_rating_percentage'
        ])

