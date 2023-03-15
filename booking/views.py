import logging

from django.conf import settings

from drf_yasg.openapi import IN_QUERY, Parameter
from drf_yasg.utils import swagger_auto_schema
from rest_framework import generics, permissions, status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from booking.models import Booking, Withdrawal
from booking.serializers import (BookingAcceptSerializer,
                                 BookingCancelSerializer,
                                 BookingDeclineSerializer,
                                 BookingPaymentSerializer,
                                 BookingPromocodeApplicationSerializer,
                                 BookingReportFileSerializer,
                                 BookingReviewSerializer,
                                 BookingSerializer)
from main.pagination import StandardResultsSetPagination
from main.permissions import IsDJ, IsEmailConfirmed
from users.models import User
from users.serializers import WithdrawalSerializer
from users.services.payment import PaymentService

logger = logging.getLogger('django')


class BookingListCreateView(generics.ListCreateAPIView):
    serializer_class = BookingSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsEmailConfirmed]

    def get_queryset(self):
        # TODO переделать на фильтр сет, чтобы небыло дублирования кода
        queryset = Booking.objects.all()
        statuses = self.request.query_params.get('status')
        if statuses is not None:
            status_params = []
            for status in statuses.split(','):
                try:
                    status_params.append(int(status))
                except ValueError:
                    pass
            queryset = queryset.filter(status__in=status_params)
        return queryset.by_booker(account=self.request.user.get_account()).order_by("-id")

    @swagger_auto_schema(
        manual_parameters=[
            Parameter('status', IN_QUERY, type='list'),
        ]
    )
    def get(self, request, *args, **kwargs):
        """ Get list of bookings """
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """ Create booking """
        return super().post(request, *args, **kwargs)


class GigListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, IsDJ]
    serializer_class = BookingSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        # TODO переделать на фильтр сет, чтобы небыло дублирования кода
        queryset = Booking.objects.all()
        statuses = self.request.query_params.get('status')
        if statuses is not None:
            status_params = []
            for status in statuses.split(','):
                try:
                    status_params.append(int(status))
                except ValueError:
                    pass
            queryset = queryset.filter(status__in=status_params)
        return queryset.by_dj(account=self.request.user.get_account()).order_by("-id")

    @swagger_auto_schema(
        manual_parameters=[
            Parameter('status', IN_QUERY, type='list'),
        ]
    )
    def get(self, request, *args, **kwargs):
        """ Get list of gigs """
        return super().get(request, *args, **kwargs)


class BookingRetrieveUpdateView(generics.RetrieveUpdateAPIView):

    serializer_class = BookingSerializer

    def get_queryset(self):
        return Booking.objects.filter_by_booker(
            account=self.request.user.get_account()
        )


class BookingDeclineView(generics.GenericAPIView):
    serializer_class = BookingDeclineSerializer

    def get_queryset(self):
        return Booking.objects.filter_by_account(account=self.request.user.get_account())

    def post(self, request, *args, **kwargs):
        instance = get_object_or_404(self.get_queryset(), pk=kwargs['pk'])
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.decline(instance, request.data["decline_comment"])
        return Response(serializer.data)


class BookingCancelView(generics.GenericAPIView):
    serializer_class = BookingCancelSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Booking.objects.filter_by_account(account=self.request.user.get_account())

    def post(self, request, *args, **kwargs):
        instance = get_object_or_404(self.get_queryset(), pk=kwargs['pk'])
        serializer = self.get_serializer(
            instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class BookingAcceptView(generics.GenericAPIView):
    serializer_class = BookingAcceptSerializer
    permission_classes = [IsAuthenticated, IsDJ]

    def get_queryset(self):
        return Booking.objects.filter_by_dj(account=self.request.user.get_account())

    def post(self, request, *args, **kwargs):
        instance = get_object_or_404(self.get_queryset(), pk=kwargs['pk'])
        serializer = self.get_serializer(
            instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.accept(instance)
        return Response(serializer.data)


class BookingReportFileCreateView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    queryset = Booking.objects.all()
    serializer_class = BookingReportFileSerializer

    def post(self, request, *args, **kwargs):
        instance = get_object_or_404(self.get_queryset(), pk=kwargs['pk'])
        serializer = self.get_serializer()
        file_name = serializer.create_booking_report_file(instance)
        return Response({'file_name': file_name})


class BookingReviewCreateView(generics.CreateAPIView):
    serializer_class = BookingReviewSerializer

    def get_queryset(self):
        return Booking.objects.get_success().by_account(account=self.request.user.get_account())


class PaymentWebhookView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        payment_service = PaymentService()
        try:
            event = payment_service.provider.Webhook.construct_event(
                payload=request.body,
                sig_header=request.headers.get('stripe-signature'),
                secret=settings.STRIPE_WEBHOOK_SECRET
            )
            if event.type == 'payment_intent.succeeded':
                payment_intent_id = event.data.object.id
                amount = event.data.object.amount
                payment_service.pay_booking(payment_intent_id, amount)
            if event.type == 'transfer.created':
                withdrawal = payment_service.withdraw(
                    event.data.object.metadata.withdrawal_id
                )
                logger.info(withdrawal)
                payment_service.payout_to_card(withdrawal)
        except ValueError as e:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except payment_service.provider.error.SignatureVerificationError as e:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_200_OK)


class AccountWebhookView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        payment_service = PaymentService()
        try:
            event = payment_service.provider.Webhook.construct_event(
                payload=request.body,
                sig_header=request.headers.get('stripe-signature'),
                secret=settings.STRIPE_ACCOUNTS_WEBHOOK_SECRET
            )
            if event.type == 'account.updated':
                if settings.LIVE_MODE and not event.livemode:
                    return Response(status=status.HTTP_400_BAD_REQUEST)
                stripe_account = event.data.object.id
                current_requirements = event.data.object.requirements.currently_due
                User.objects.update_stripe_profile_status(
                    stripe_account,
                    current_requirements
                )
        except ValueError as e:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except payment_service.provider.error.SignatureVerificationError as e:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_200_OK)


class BookingPaymentView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = BookingPaymentSerializer

    def get_queryset(self):
        return Booking.objects.filter(
            pk=self.kwargs.get('pk')
        ).by_booker(
            account=self.request.user.get_account()
        )

    def post(self, request, *args, **kwargs):
        instance = get_object_or_404(self.get_queryset(), pk=kwargs['pk'])
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        res = serializer.pay_for_booking(instance)
        return Response(data=res, status=status.HTTP_200_OK)


class BookingPromocodeApplicationView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = BookingPromocodeApplicationSerializer

    def get_queryset(self):
        return Booking.objects.filter(
            pk=self.kwargs.get('pk')
        ).by_account(
            account=self.request.user.get_account()
        )

    def post(self, request, *args, **kwargs):
        instance = get_object_or_404(self.get_queryset(), pk=kwargs['pk'])
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.apply(instance)
        return Response(status=status.HTTP_200_OK)


class WithdrawalRetrieveView(generics.RetrieveAPIView):

    serializer_class = WithdrawalSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Withdrawal.objects.filter(user=self.request.user)
