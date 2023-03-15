from django.contrib.postgres.search import SearchVector

from django_filters.rest_framework import FilterSet
from django_filters.rest_framework.filters import CharFilter, DateFilter
from drf_yasg import openapi
from drf_yasg.openapi import Parameter
from drf_yasg.utils import swagger_auto_schema
from rest_framework import generics
from rest_framework.filters import OrderingFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from booking.admin_serializers import (BookingChangeRecordAdminSerializer,
                                       BookingDeclineAdminSerializer,
                                       PromocodeAdminSerializer,
                                       TransactionAdminSerializer,
                                       WithdrawalAdminSerializer)
from booking.models import (Booking,
                            BookingChangeRecord,
                            Promocode,
                            Transaction,
                            Withdrawal)
from booking.serializers import BookingSerializer
from main.pagination import StandardResultsSetPagination
from users.enums import GigTypes, Music


class BookingAdminFilterSet(FilterSet):

    search = CharFilter(method="search_bookings")

    start_date = DateFilter(field_name="date", lookup_expr="gte")
    end_date = DateFilter(field_name="date", lookup_expr="lte")

    status = CharFilter(method="filter_by_status")
    booker_account_id = CharFilter(field_name="account_booker__uuid")
    dj_account_id = CharFilter(field_name="account_dj__uuid")

    class Meta:
        model = Booking
        fields = [
            'id',
            'status',
            'booker_account_id',
            'dj_account_id',
            'date'
        ]

    def search_bookings(self, queryset, name, search):

        if search is not None:

            # by various data

            search_queryset = queryset.annotate(
                search=SearchVector(
                    "location_state",
                    "location_city",
                    "comment",
                    "decline_comment",
                    "account_booker__user__first_name",
                    "account_booker__user__last_name",
                    "account_booker__user__email",
                    "account_booker__uuid",
                    "account_booker__booker_profile__state",
                    "account_booker__booker_profile__city",
                    "account_dj__uuid",
                    "account_dj__user__first_name",
                    "account_dj__user__last_name",
                    "account_dj__user__email",
                    "account_dj__dj_profile__stage_name",
                    "account_dj__dj_profile__state",
                    "account_dj__dj_profile__city",
                    "account_dj__dj_profile__additional_state",
                    "account_dj__dj_profile__additional_city",
                    "account_dj__dj_profile__note",
                    "account_dj__dj_profile__social_fb",
                    "account_dj__dj_profile__social_inst",
                    "account_dj__dj_profile__social_eventbrite",
                    "account_dj__dj_profile__social_pinterest",
                    "account_dj__dj_profile__social_sound_cloud"
                )
            ).filter(
                search=search
            )

            # by status name

            queryset_filtered = queryset

            status_filter = []
            for i, name in [(i.value, i.name.lower().replace('_', ' ')) for i in Booking.Status]:
                if search.lower() in name:
                    status_filter.append(i)

            if status_filter:
                queryset_filtered = queryset_filtered.filter(status__in=status_filter)

            # by gig type name

            gigtype_filter = []
            for i, name in [(i.value, i.name.lower().replace('_', ' ')) for i in GigTypes]:
                if search.lower() in name:
                    gigtype_filter.append(i)

            if gigtype_filter:
                queryset_filtered = queryset_filtered.filter(gig_type__in=gigtype_filter)

            # by music choices

            music_filter = []
            for i, name in [(i.value, i.name.lower().replace('_', ' ')) for i in Music]:
                if search.lower() in name:
                    music_filter.append(i)

            if music_filter:
                queryset_filtered = queryset_filtered.filter(music_list__overlap=music_filter)

            if not (status_filter or gigtype_filter or music_filter):
                queryset = search_queryset
            else:
                queryset = search_queryset | queryset_filtered

        return queryset.order_by('-id')

    def filter_by_status(self, queryset, name, statuses):
        if statuses is not None:
            status_params = []
            for status in statuses.split(','):
                try:
                    status_params.append(int(status))
                except ValueError:
                    pass
            queryset = queryset.filter(status__in=status_params)
        return queryset.order_by('-id')


class BookingListAdminView(generics.ListAPIView):

    queryset = Booking.objects.all().order_by("-id")
    permission_classes = [IsAuthenticated, IsAdminUser]
    serializer_class = BookingSerializer
    filterset_class = BookingAdminFilterSet
    filter_backends = [OrderingFilter]
    # TODO: Указать для каких полей нужна сортировка
    ordering_fields = "__all__"
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        queryset = super().get_queryset()
        return self.filterset_class(self.request.GET, queryset=queryset).qs

    @swagger_auto_schema(
        manual_parameters=[
            Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            Parameter('start_date', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            Parameter('end_date', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            Parameter('status', openapi.IN_QUERY, type='list'),
            Parameter('booker_account_id', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            Parameter('dj_account_id', openapi.IN_QUERY, type=openapi.TYPE_STRING)
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class BookingRetrieveAdminView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    serializer_class = BookingSerializer
    queryset = Booking.objects.all()


class BookingDeclineAdminView(generics.GenericAPIView):
    queryset = Booking.objects.all()
    serializer_class = BookingDeclineAdminSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request, *args, **kwargs):
        instance = get_object_or_404(self.get_queryset(), pk=kwargs['pk'])
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.decline(instance, request.data["decline_comment"])
        return Response(serializer.data)


class WithdrawalAdminFilterSet(FilterSet):

    status = CharFilter(method="filter_by_status")

    class Meta:
        model = Withdrawal
        fields = [
            'id',
            'user',
            'status',
            'destination_card',
        ]

    def filter_by_status(self, queryset, name, statuses):
        if statuses is not None:
            status_params = []
            for status in statuses.split(','):
                try:
                    status_params.append(int(status))
                except ValueError:
                    pass
            queryset = queryset.filter(status__in=status_params)
        return queryset.order_by('-id')


class WithdrawalListAdminView(generics.ListAPIView):
    serializer_class = WithdrawalAdminSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
    pagination_class = StandardResultsSetPagination
    filterset_class = WithdrawalAdminFilterSet

    def get_queryset(self):
        queryset = Withdrawal.objects.all().order_by("-pk")
        return self.filterset_class(self.request.GET, queryset=queryset).qs

    @swagger_auto_schema(
        manual_parameters=[
            Parameter('id', openapi.IN_QUERY, type='integer'),
            Parameter('status', openapi.IN_QUERY, type='list'),
            Parameter('destination_card', openapi.IN_QUERY, type='str')
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class WithdrawalRetrieveAdminView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    serializer_class = WithdrawalAdminSerializer
    queryset = Withdrawal.objects.all()


class TransactionAdminFilterSet(FilterSet):

    purpose = CharFilter(method="filter_by_purpose")
    search = CharFilter(method="filter_by_name")

    class Meta:
        model = Transaction
        fields = [
            'id',
            'user',
            'purpose',
            'entity',
            'entity_pk',
        ]

    def filter_by_name(self, queryset, name, search):
        if search is not None:
            queryset = queryset.annotate(
                search=SearchVector(
                    "user__first_name",
                    "user__last_name",
                    "user__dj_profile__stage_name",
                    "user__id"
                )
            ).filter(
                search=search
            )
        return queryset.order_by('-id')

    def filter_by_purpose(self, queryset, name, purposes):
        if purposes is not None:
            purpose_params = []
            for purpose in purposes.split(','):
                try:
                    purpose_params.append(int(purpose))
                except ValueError:
                    pass
            queryset = queryset.filter(purpose__in=purpose_params)
        return queryset.order_by('-id')


class TransactionListAdminView(generics.ListAPIView):
    serializer_class = TransactionAdminSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
    pagination_class = StandardResultsSetPagination
    filterset_class = TransactionAdminFilterSet

    def get_queryset(self):
        queryset = Transaction.objects.all().order_by("-pk")
        return self.filterset_class(self.request.GET, queryset=queryset).qs

    @swagger_auto_schema(
        manual_parameters=[
            Parameter('id', openapi.IN_QUERY, type='integer'),
            Parameter('search', openapi.IN_QUERY, type='string'),
            Parameter('user', openapi.IN_QUERY, type='integer'),
            Parameter('purpose', openapi.IN_QUERY, type='list'),
            Parameter('entity', openapi.IN_QUERY, type='string'),
            Parameter('entity_pk', openapi.IN_QUERY, type='integer')
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class TransactionRetrieveAdminView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    serializer_class = TransactionAdminSerializer
    queryset = Transaction.objects.all()


class BookingChangeRecordAdminListView(generics.ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = BookingChangeRecordAdminSerializer
    filter_backends = [OrderingFilter]
    ordering_fields = ['created_at']
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return BookingChangeRecord.objects.filter(booking=self.request.query_params.get('booking_id'))


class PromocodeAdminFilterSet(FilterSet):

    class Meta:
        model = Promocode
        fields = [
            'id',
            'code',
            'promocode_type',
            'is_active'
        ]


class PromocodeListCreateAdminView(generics.ListCreateAPIView):
    serializer_class = PromocodeAdminSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
    pagination_class = StandardResultsSetPagination
    filterset_class = PromocodeAdminFilterSet

    def get_queryset(self):
        queryset = Promocode.objects.all()
        return self.filterset_class(self.request.GET, queryset=queryset).qs

    @swagger_auto_schema(
        manual_parameters=[
            Parameter('id', openapi.IN_QUERY, type='integer'),
            Parameter('code', openapi.IN_QUERY, type='str'),
            Parameter('promocode_type', openapi.IN_QUERY, type='integer'),
            Parameter('is_active', openapi.IN_QUERY, type='integer'),
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PromocodeRetrieveUpdateDestroyAdminView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    serializer_class = PromocodeAdminSerializer
    queryset = Promocode.objects.all()
