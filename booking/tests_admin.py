import random
from datetime import timedelta

from django.utils import timezone

from rest_framework import status
from rest_framework.reverse import reverse

from booking.enums import PromocodeType, TransactionPurposes
from booking.models import Booking, Promocode
from users.models import Account
from utils.test import AuthClientTestCase


class BookingAdminTestCase(AuthClientTestCase):

    def setUp(self):
        super().setUp()
        self.test_data_service.create_dj_profiles(count=5)
        self.test_data_service.create_booker_users(count=5)
        self.test_data_service.create_random_bookings(count=5)

    def test_access_by_user_without_permissions(self):
        response = self.client.get(
            reverse('booking:booking_admin_list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_bookings(self):
        response = self.staff_client.get(
            reverse('booking:booking_admin_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(response.data["count"], 0)

    def test_filter_bookings_by_dj_account_id(self):
        dj_account = random.choice(Account.objects.filter(dj_profile__isnull=False))
        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'dj_account_id': dj_account.uuid})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], Booking.objects.filter(
            account_dj__uuid=dj_account.uuid).count())

    def test_filter_bookings_by_booker_account_id(self):
        booker_account = random.choice(Account.objects.filter(booker_profile__isnull=False))
        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'booker_account_id': booker_account.uuid})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], Booking.objects.filter(
            account_booker__uuid=booker_account.uuid).count())

    def test_get_booking_list_with_ordering_parameter(self):
        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'ordering': "id"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        prev_booking = None
        for booking in response.data["results"]:
            if prev_booking is None:
                prev_booking = booking
                continue
            self.assertGreater(booking["id"], prev_booking["id"])

    def test_get_booking_list_with_filter_parameter(self):
        booking = Booking.objects.first()
        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'id': booking.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], booking.pk)

    def test_get_booking_list_with_order_and_filter_parameter(self):
        response = self.staff_client.get(
            reverse('booking:booking_admin_list'),
            {
                'ordering': "id",
                'id': "[0, 1, 2, 3, 4]",
            }
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 5)
        bookings = response.data["results"]
        for i in range(response.data["count"]):
            if i == 0:
                continue
            self.assertGreater(bookings[i]["id"], bookings[i - 1]["id"])

    def test_decline_booking(self):
        data = {'decline_comment': "Some comment"}
        booking = random.choice(Booking.objects.all())
        response = self.staff_client.post(
            reverse('booking:booking_admin_decline', args=[booking.pk]), data=data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        declined_booking = Booking.objects.get(pk=booking.pk)
        self.assertEqual(declined_booking.status, Booking.Status.DECLINED_BY_STAFF)
        self.assertEqual(declined_booking.decline_comment, data['decline_comment'])

    def test_get_booking_retrieve(self):
        booking = random.choice(Booking.objects.all())
        response = self.staff_client.get(
            reverse('booking:booking_admin_retrieve', args=[booking.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], booking.pk)

    def test_get_booking_list_status_changes(self):

        booking = Booking.objects.last()

        response = self.staff_client.get(reverse('booking:booking_admin_changes'), {'booking_id': booking.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

        booking = Booking.objects.first()
        booking.status = Booking.Status.PAID
        booking.save()

        booking.status = Booking.Status.COMPLETED
        booking.save()

        response = self.staff_client.get(reverse('booking:booking_admin_changes'), {'booking_id': booking.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 3)

    def test_filter_bookings_by_start_date_end_date(self):

        response = self.staff_client.get(
            reverse('booking:booking_admin_list'),
            {
                'start_date': timezone.now().date() + timedelta(days=1),  # 2021-06-16
                'end_date': timezone.now().date() + timedelta(days=3)
            }
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 3)

    def test_filter_bookings_using_search_field(self):

         # by Music

        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'search': '90s'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'search': 'House'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 5)

         # by status
        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'search': 'Not paid'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 5)

        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'search': 'canceled'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

         # by gig type
        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'search': 'party'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 5)

        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'search': 'other'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

        # by any of the booking, dj or booker text fields

        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'search': 'Comment 1'} # by comment
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {
                'search': Booking.objects.first().account_dj.user.email
            } # by email
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(response.data["count"], 0)

        # in case of inner params intersection

        b = Booking.objects.all().first()
        b.comment = 'party'
        b.save()

        response = self.staff_client.get(
            reverse('booking:booking_admin_list'), {'search': 'party'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 5)

    def test_get_admin_booking_changes_list(self):

        booking = random.choice(Booking.objects.all())

        response = self.staff_client.get(reverse('booking:booking_admin_changes'), {'booking_id': booking.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

        booking.pay()

        response = self.staff_client.get(reverse('booking:booking_admin_changes'), {'booking_id': booking.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)


class WithdrawalAdminTestCase(AuthClientTestCase):
    """
    TODO: possibly move all Withdrawal code to booking from users
    """

    def setUp(self):
        super().setUp()

    def test_withdrawal_list(self):
        for _ in range(5):
            self.test_data_service.create_random_withdrawal()
        response = self.staff_client.get(reverse('booking:withdrawal_admin_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 5)

    def test_withdrawal_retrieve(self):

        withdrawals = []
        for _ in range(5):
            withdrawals.append(self.test_data_service.create_random_withdrawal())

        response = self.staff_client.get(
            reverse('booking:withdrawal_admin_retrieve', args=[withdrawals[-1].pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], withdrawals[-1].pk)


class TransactionAdminTestCase(AuthClientTestCase):

    def setUp(self):
        super().setUp()

    def test_transaction_list(self):
        for _ in range(5):
            self.test_data_service.create_random_transaction()
        response = self.staff_client.get(reverse('booking:transaction_admin_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 5)

    def test_transaction_list_custom_columns_running_balance_in_output(self):

        user = self.test_data_service.create_random_dj_user()

        TRANSACTIONS = [
            (TransactionPurposes.PAYMENT, 2147200, self.booker_user),
            (TransactionPurposes.BOOKING_ESCROW, -2039841, self.booker_user),
            (TransactionPurposes.BOOKING_FEE_FOR_BOOKER, -107359, self.booker_user),
            (TransactionPurposes.PAYMENT_TO_USER, 2039841, self.dj_user),
            (TransactionPurposes.BOOKING_FEE_FOR_DJ, -107359, self.dj_user),
            (TransactionPurposes.WITHDRAWAL, -300, self.dj_user),
            (TransactionPurposes.PAYMENT, 10000, user),
        ]

        for ti in TRANSACTIONS:
            self.test_data_service.create_random_transaction(
                purpose=ti[0],
                amount=ti[1],
                user=ti[2]
            )

        response = self.staff_client.get(reverse('booking:transaction_admin_list'), {'user': self.booker_user.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 3)

        # last at the top, first at the bottom
        self.assertEqual(response.data['results'][0]['withdrawals'], -107359)
        self.assertEqual(response.data['results'][0]['deposits'], 0)
        self.assertEqual(response.data['results'][0]['running_balance'], 0)

        self.assertEqual(response.data['results'][1]['withdrawals'], -2039841)
        self.assertEqual(response.data['results'][1]['deposits'], 0)
        self.assertEqual(response.data['results'][1]['running_balance'], 107359)

        self.assertEqual(response.data['results'][2]['withdrawals'], 0)
        self.assertEqual(response.data['results'][2]['deposits'], 2147200)
        self.assertEqual(response.data['results'][2]['running_balance'], 2147200)

        # for dj
        response = self.staff_client.get(reverse('booking:transaction_admin_list'), {'user': self.dj_user.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 3)

        # last at the top, first at the bottom
        self.assertEqual(response.data['results'][0]['withdrawals'], -300)
        self.assertEqual(response.data['results'][0]['deposits'], 0)
        self.assertEqual(response.data['results'][0]['running_balance'], 1932182)

        self.assertEqual(response.data['results'][1]['withdrawals'], -107359)
        self.assertEqual(response.data['results'][1]['deposits'], 0)
        self.assertEqual(response.data['results'][1]['running_balance'], 1932482)

        self.assertEqual(response.data['results'][2]['withdrawals'], 0)
        self.assertEqual(response.data['results'][2]['deposits'], 2039841)
        self.assertEqual(response.data['results'][2]['running_balance'], 2039841)

        # total

        response = self.staff_client.get(reverse('booking:transaction_admin_list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 7)

        # last at the top, first at the bottom
        self.assertEqual(response.data['results'][0]['withdrawals'], 0)
        self.assertEqual(response.data['results'][0]['deposits'], 10000)
        self.assertEqual(response.data['results'][0]['running_balance'], 1942182)  # it means sum of all users balances at this point

    def test_transaction_retrieve(self):

        transactions = []
        for _ in range(5):
            transactions.append(self.test_data_service.create_random_transaction())

        response = self.staff_client.get(
            reverse('booking:transaction_admin_retrieve', args=[transactions[-1].pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], transactions[-1].pk)


class PromocodeAdminTestCase(AuthClientTestCase):

    def setUp(self):
        super().setUp()

        for _ in range(5):
            self.test_data_service.create_random_promocode()

    def test_admin_list_promocode(self):

        response = self.staff_client.get(
            reverse('booking:promocode_admin_list_create')
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 5)

    def test_admin_promocode_create(self):

        data = {
            'amount': "100",
            'promocode_type': PromocodeType.FIXED.value,
        }

        response = self.staff_client.post(
            reverse('booking:promocode_admin_list_create'),
            data=data
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Promocode.objects.filter(pk=response.data['id']).exists())

        # percent errors

        data = {
            'amount': 101,
            'promocode_type': PromocodeType.PERCENT.value,
            'max_application_count': 3,
            'start_date': '2021-01-01 00:00:00',
            'end_date': '2021-01-02 00:00:00'
        }

        response = self.staff_client.post(
            reverse('booking:promocode_admin_list_create'),
            data=data
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(str(response.data['amount'][0]), 'Percent should not be more than 100')

        data = {
            'amount': 0,
            'promocode_type': PromocodeType.PERCENT.value,
        }

        response = self.staff_client.post(
            reverse('booking:promocode_admin_list_create'),
            data=data
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(str(response.data['amount'][0]), 'Percent should not be less than 0')

    def test_admin_promocode_retrieve(self):

        pr = random.choice(Promocode.objects.all())

        response = self.staff_client.get(
            reverse('booking:promocode_admin_retrieve_update_destroy', args=[pr.pk]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], pr.pk)

    def test_admin_promocode_update(self):

        pr = random.choice(Promocode.objects.all())
        pr.is_active = True
        pr.save()

        response = self.staff_client.put(
            reverse('booking:promocode_admin_retrieve_update_destroy', args=[pr.pk]),
            {'is_active': False}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        pr = Promocode.objects.get(pk=pr.pk)
        self.assertFalse(pr.is_active)

    def test_admin_promocode_delete(self):

        pr = random.choice(Promocode.objects.all())

        response = self.staff_client.delete(
            reverse('booking:promocode_admin_retrieve_update_destroy', args=[pr.pk]))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
