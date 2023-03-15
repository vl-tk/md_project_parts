from django.urls import path

from rest_framework import routers

from booking.admin_views import (BookingChangeRecordAdminListView,
                                 BookingDeclineAdminView,
                                 BookingListAdminView,
                                 BookingRetrieveAdminView,
                                 PromocodeListCreateAdminView,
                                 PromocodeRetrieveUpdateDestroyAdminView,
                                 TransactionListAdminView,
                                 TransactionRetrieveAdminView,
                                 WithdrawalListAdminView,
                                 WithdrawalRetrieveAdminView)
from booking.views import (AccountWebhookView,
                           BookingAcceptView,
                           BookingCancelView,
                           BookingDeclineView,
                           BookingListCreateView,
                           BookingPaymentView,
                           BookingPromocodeApplicationView,
                           BookingReportFileCreateView,
                           BookingRetrieveUpdateView,
                           BookingReviewCreateView,
                           GigListView,
                           PaymentWebhookView,
                           WithdrawalRetrieveView)

app_name = 'Booking api'

router = routers.SimpleRouter()

urlpatterns = [
    path('<int:pk>', BookingRetrieveUpdateView.as_view(), name='booking_retrieve_update'),
    path('<int:pk>/pay', BookingPaymentView.as_view(), name='booking_pay'),
    path('<int:pk>/promocode', BookingPromocodeApplicationView.as_view(), name='booking_promocode_application'),
    path('', BookingListCreateView.as_view(), name='booking_list_create'),
    path('gigs', GigListView.as_view(), name='gig_list'),
    path('<int:pk>/decline', BookingDeclineView.as_view(), name='booking_decline'),
    path('<int:pk>/cancel', BookingCancelView.as_view(), name='booking_cancel'),
    path('<int:pk>/accept', BookingAcceptView.as_view(), name='booking_accept'),
    path('<int:pk>/review', BookingReviewCreateView.as_view(), name='booking_review_create'),
    path('<int:pk>/report_file', BookingReportFileCreateView.as_view(), name='create_report_file'),
    path('withdrawal/<int:pk>', WithdrawalRetrieveView.as_view(), name="withdrawal_retrieve"),
    path('payment_webhook', PaymentWebhookView.as_view(), name='booking_payment_webhook'),
    path('account_webhook', AccountWebhookView.as_view(), name='booking_account_webhook'),
    path('admin/list/', BookingListAdminView.as_view(), name='booking_admin_list'),
    path('admin/list/<int:pk>/', BookingRetrieveAdminView.as_view(), name='booking_admin_retrieve'),
    path('admin/list/changes', BookingChangeRecordAdminListView.as_view(), name='booking_admin_changes'),
    path('admin/<int:pk>/decline', BookingDeclineAdminView.as_view(), name='booking_admin_decline'),
    path('admin/withdrawal/list/', WithdrawalListAdminView.as_view(), name='withdrawal_admin_list'),
    path('admin/withdrawal/list/<int:pk>', WithdrawalRetrieveAdminView.as_view(), name='withdrawal_admin_retrieve'),
    path('admin/transaction/list/', TransactionListAdminView.as_view(), name='transaction_admin_list'),
    path('admin/transaction/list/<int:pk>', TransactionRetrieveAdminView.as_view(), name='transaction_admin_retrieve'),
    path('admin/promocode/list/', PromocodeListCreateAdminView.as_view(), name='promocode_admin_list_create'),
    path('admin/promocode/list/<int:pk>', PromocodeRetrieveUpdateDestroyAdminView.as_view(), name='promocode_admin_retrieve_update_destroy'),
]

urlpatterns += router.urls
