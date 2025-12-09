from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.cache import cache
from django.db.models import Sum, F, Count, Q
from .models import Invoice, Person
from .serializers import InvoiceSerializer
import hashlib

MONTH_NAMES = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'
]


class InvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = InvoiceSerializer

    # Base queryset
    def get_queryset(self):
        """
        Centralized queryset with select_related + only for performance.
        """
        return (
            Invoice.objects
            .select_related('person')
            .only(
                'id', 'invoice_type', 'amount', 'date', 'is_paid',
                'person__name', 'person__role'
            )
        )

    # ---------------------------------------------------
    # Pagination (skip / take style)
    # ---------------------------------------------------
    def paginate_queryset(self, queryset):
        """
        Custom skip/take pagination integrated with DRF's expected signature.
        Uses query params: ?skip=0&take=10
        """
        request = self.request
        try:
            skip = int(request.query_params.get('skip', 0))
            take = int(request.query_params.get('take', 10))
            if skip < 0 or take < 1:
                raise ValueError
        except (TypeError, ValueError):
            skip, take = 0, 10

        # Optional: enforce a maximum page size
        if take > 1000:
            take = 1000

        return queryset[skip:skip + take]

    # ---------------------------------------------------
    # Redis Helpers
    # ---------------------------------------------------
    def get_cache_key(self, prefix, *args, **kwargs):
        """
        Generate strong Redis keys based on prefix + args + kwargs.
        """
        raw = prefix + str(args) + str(kwargs)
        hashed = hashlib.md5(raw.encode('utf-8')).hexdigest()
        return f"rksuppliers:invoice:{prefix}:{hashed}"

    def clear_cache(self):
        """
        Clear all invoice-related cache.
        NOTE: delete_pattern can be slow if there are many keys.
        """
        try:
            cache.delete_pattern("rksuppliers:invoice:*")
            print("🗑 Redis cache cleared")
        except Exception as e:
            print(f"⚠️ Cache clear error: {e}")

    # ---------------------------------------------------
    # Common filter helper
    # ---------------------------------------------------
    def apply_common_filters(self, qs, filters):
        """
        Filters for purchase/sell/dashboard endpoints.
        Supported filters:
        - month (name or int, depending on use)
        - year
        - search (person name)
        - person_id
        - date_from, date_to
        - is_paid (true/false/1/0)
        """
        month = filters.get('month')
        year = filters.get('year')
        search = filters.get('search', '').strip()
        person_id = filters.get('person_id')
        date_from = filters.get('date_from')
        date_to = filters.get('date_to')
        is_paid_param = filters.get('is_paid')

        # Month can be name or int; we support both
        if month and month != "All":
            try:
                if month.isdigit():
                    month_num = int(month)
                else:
                    month_num = MONTH_NAMES.index(month) + 1
                qs = qs.filter(date__month=month_num)
            except (ValueError, IndexError):
                # Invalid month, ignore filter
                pass

        if year and year != "All":
            try:
                qs = qs.filter(date__year=int(year))
            except (ValueError, TypeError):
                pass

        if search:
            qs = qs.filter(person__name__icontains=search)

        if person_id:
            qs = qs.filter(person_id=person_id)

        if date_from:
            qs = qs.filter(date__gte=date_from)

        if date_to:
            qs = qs.filter(date__lte=date_to)

        if is_paid_param in ['true', '1']:
            qs = qs.filter(is_paid=True)
        elif is_paid_param in ['false', '0']:
            qs = qs.filter(is_paid=False)

        return qs

    # ---------------------------------------------------
    # List API with Caching
    # ---------------------------------------------------
    def list(self, request, *args, **kwargs):
        filters = request.query_params.dict()
        cache_key = self.get_cache_key("list", str(sorted(filters.items())))

        cached = cache.get(cache_key)
        if cached:
            print("🟢 Redis HIT (list)")
            return Response(cached)

        print("🔴 Redis MISS (list)")

        queryset = self.get_queryset()

        # You can optionally apply the same filters for list if desired
        # queryset = self.apply_common_filters(queryset, filters)

        # Slice AFTER filters (but BEFORE aggregation if you want totals over full set)
        full_qs = queryset  # full filtered queryset (no pagination)
        paginated_qs = self.paginate_queryset(full_qs)

        serializer = self.get_serializer(paginated_qs, many=True)

        # Use Q(...) in filter for aggregates (CORRECT way)
        totals = full_qs.aggregate(
            total_purchase=Sum('amount', filter=Q(invoice_type='purchase')),
            total_sell=Sum('amount', filter=Q(invoice_type='sale'))
        )

        data = {
            'total_purchase': float(totals['total_purchase'] or 0),
            'total_sell': float(totals['total_sell'] or 0),
            'results': serializer.data,
        }

        cache.set(cache_key, data, 300)
        return Response(data)

    # ---------------------------------------------------
    # Purchase / Sell Quick Lists
    # ---------------------------------------------------
    @action(detail=False, methods=['get'])
    def purchase(self, request):
        filters = request.query_params.dict()
        cache_key = self.get_cache_key("purchase", str(sorted(filters.items())))

        cached = cache.get(cache_key)
        if cached:
            print("🟢 Redis HIT (purchase)")
            return Response(cached)

        print("🔴 Redis MISS (purchase)")

        qs = (
            Invoice.objects
            .filter(invoice_type='purchase')
            .select_related('person')
        )

        qs = self.apply_common_filters(qs, filters)
        qs = qs.order_by('-date')

        # Aggregates on full filtered queryset
        totals = qs.aggregate(
            total_amount=Sum('amount'),
            total_pending=Sum('amount', filter=Q(is_paid=False))
        )

        total_count = qs.count()

        paginated_qs = self.paginate_queryset(qs)
        data_list = self.get_serializer(paginated_qs, many=True).data

        data = {
            'total_amount': float(totals['total_amount'] or 0),
            'total_pending': float(totals['total_pending'] or 0),
            'count': total_count,
            'filters_applied': {
                'month': filters.get('month'),
                'year': filters.get('year'),
                'search': filters.get('search', '').strip(),
            },
            'results': data_list,
        }

        cache.set(cache_key, data, 300)
        print(f"📦 Purchase: {len(data_list)}/{total_count} items")
        return Response(data)

    @action(detail=False, methods=['get'])
    def sell(self, request):
        filters = request.query_params.dict()
        cache_key = self.get_cache_key("sell", str(sorted(filters.items())))

        cached = cache.get(cache_key)
        if cached:
            print("🟢 Redis HIT (sell)")
            return Response(cached)

        print("🔴 Redis MISS (sell)")

        qs = (
            Invoice.objects
            .filter(invoice_type='sale')
            .select_related('person')
        )

        qs = self.apply_common_filters(qs, filters)
        qs = qs.order_by('-date')

        totals = qs.aggregate(
            total_amount=Sum('amount'),
            total_pending=Sum('amount', filter=Q(is_paid=False))
        )

        total_count = qs.count()
        paginated_qs = self.paginate_queryset(qs)
        data_list = self.get_serializer(paginated_qs, many=True).data

        data = {
            'total_amount': float(totals['total_amount'] or 0),
            'total_pending': float(totals['total_pending'] or 0),
            'count': total_count,
            'filters_applied': {
                'month': filters.get('month'),
                'year': filters.get('year'),
                'search': filters.get('search', '').strip(),
            },
            'results': data_list,
        }

        cache.set(cache_key, data, 300)
        print(f"📦 Sell: {len(data_list)}/{total_count} items")
        return Response(data)

    # ---------------------------------------------------
    # CRUD with Cache Invalidation
    # ---------------------------------------------------
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.clear_cache()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None, *args, **kwargs):
        cache_key = self.get_cache_key("retrieve", pk)

        cached = cache.get(cache_key)
        if cached:
            print("🟢 Redis HIT (retrieve)")
            return Response(cached)

        print("🔴 Redis MISS (retrieve)")
        response = super().retrieve(request, pk, *args, **kwargs)
        cache.set(cache_key, response.data, 300)
        return response

    def update(self, request, pk=None, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        response = super().update(request, pk, partial=partial, *args, **kwargs)
        self.clear_cache()
        return response

    def partial_update(self, request, pk=None, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, pk, *args, **kwargs)

    def destroy(self, request, pk=None, *args, **kwargs):
        response = super().destroy(request, pk, *args, **kwargs)
        self.clear_cache()
        return response

    # ---------------------------------------------------
    # Mark all invoices as paid
    # ---------------------------------------------------
    @action(detail=False, methods=['post'], url_path='mark_all_paid/(?P<person_id>[^/.]+)')
    def mark_all_paid(self, request, person_id=None):
        try:
            person = Person.objects.get(id=person_id)
        except Person.DoesNotExist:
            return Response({'detail': 'Person not found.'}, status=status.HTTP_404_NOT_FOUND)

        updated = Invoice.objects.filter(person=person, is_paid=False).update(is_paid=True)
        self.clear_cache()

        return Response({'detail': f'{updated} invoices marked as paid.'})

    # ---------------------------------------------------
    # Person names (for dropdowns etc.)
    # ---------------------------------------------------
    @action(detail=False, methods=['get'], url_path='person-names')
    def person_names(self, request):
        cache_key = self.get_cache_key("person_names")

        cached = cache.get(cache_key)
        if cached:
            return Response(cached)

        names = list(Person.objects.values_list('name', flat=True))
        data = {"person_names": names}

        cache.set(cache_key, data, 600)
        return Response(data)

    # ---------------------------------------------------
    # DASHBOARD
    # ---------------------------------------------------
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        filters = request.query_params.dict()
        cache_key = self.get_cache_key("dashboard", str(sorted(filters.items())))

        cached = cache.get(cache_key)
        if cached:
            print("🟢 Redis HIT (dashboard)")
            return Response(cached)

        print("🔴 Redis MISS (dashboard)")

        # Base queryset for dashboard
        qs = Invoice.objects.select_related('person')
        qs = self.apply_common_filters(qs, filters)

        # --- TOTALS ---
        totals = qs.aggregate(
            total_purchase=Sum('amount', filter=Q(invoice_type='purchase')),
            total_sales=Sum('amount', filter=Q(invoice_type='sale')),
            pending_purchase=Sum('amount', filter=Q(invoice_type='purchase', is_paid=False)),
            pending_sales=Sum('amount', filter=Q(invoice_type='sale', is_paid=False)),
        )

        # --- RECENT 5 ---
        recent_purchases = self.get_serializer(
            qs.filter(invoice_type='purchase').order_by('-date')[:5],
            many=True
        ).data

        recent_sales = self.get_serializer(
            qs.filter(invoice_type='sale').order_by('-date')[:5],
            many=True
        ).data

        # --- PENDING GROUPED BY VENDOR ---
        pending = (
            qs.filter(is_paid=False)
            .values('person__name', 'invoice_type')
            .annotate(
                invoice_count=Count('id'),
                total_amount=Sum('amount')
            )
            .order_by('-total_amount')[:5]
        )

        data = {
            "totals": {
                "total_purchase": float(totals['total_purchase'] or 0),
                "total_sales": float(totals['total_sales'] or 0),
                "pending_purchase": float(totals['pending_purchase'] or 0),
                "pending_sales": float(totals['pending_sales'] or 0),
            },
            "recent_purchases": recent_purchases,
            "recent_sales": recent_sales,
            "pending": list(pending),
        }

        cache.set(cache_key, data, 300)
        print("✅ Dashboard computed correctly")
        return Response(data)
