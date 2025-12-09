from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.cache import cache
from django.db.models import Sum, F, Value, Prefetch, Count
from .models import Invoice, Person
from .serializers import InvoiceSerializer
import hashlib

MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
               'July', 'August', 'September', 'October', 'November', 'December']


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.select_related('person').only(
        'id', 'invoice_type', 'amount', 'date', 'is_paid', 'person__name', 'person__role'
    )
    serializer_class = InvoiceSerializer

    # ---------------------------------------------------
    # Pagination
    # ---------------------------------------------------
    def paginate_queryset(self, queryset, request):
        try:
            skip = int(request.query_params.get('skip', 0))
            take = int(request.query_params.get('take', 10))
            if skip < 0 or take < 1:
                raise ValueError
        except ValueError:
            skip, take = 0, 10
        return queryset[skip:skip + take]

    # ---------------------------------------------------
    # Redis Helpers
    # ---------------------------------------------------
    def get_cache_key(self, prefix, *args, **kwargs):
        """Generate strong Redis keys"""
        raw = prefix + str(args) + str(kwargs)
        hashed = hashlib.md5(raw.encode()).hexdigest()
        return f"rksuppliers:invoice:{prefix}:{hashed}"

    def clear_cache(self):
        """Clear all invoice-related cache"""
        try:
            cache.delete_pattern("rksuppliers:invoice:*")
            print("🗑 Redis cache cleared")
        except Exception as e:
            print(f"⚠️ Cache clear error: {e}")

    # ---------------------------------------------------
    # List API with Caching (OPTIMIZED)
    # ---------------------------------------------------
    def list(self, request):
        filters = request.query_params.dict()
        cache_key = self.get_cache_key("list", str(sorted(filters.items())))

        cached = cache.get(cache_key)
        if cached:
            print("🟢 Redis HIT (list)")
            return Response(cached)

        print("🔴 Redis MISS (list)")

        queryset = self.filter_queryset(self.get_queryset())
        paginated = self.paginate_queryset(queryset, request)
        serializer = self.get_serializer(paginated, many=True)

        # ✅ OPTIMIZED: Use queryset directly, not filtered_qs
        filtered_qs = queryset  # Already filtered
        
        # ✅ SINGLE QUERY: Get both totals in one query
        totals = filtered_qs.aggregate(
            total_purchase=Sum('amount', filter=F('invoice_type')=='purchase'),
            total_sell=Sum('amount', filter=F('invoice_type')=='sale')
        )

        data = {
            'total_purchase': float(totals['total_purchase'] or 0),
            'total_sell': float(totals['total_sell'] or 0),
            'results': serializer.data,
        }

        cache.set(cache_key, data, 300)
        return Response(data)

    # ---------------------------------------------------
    # Purchase / Sell Quick Lists (OPTIMIZED)
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
        
        qs = Invoice.objects.filter(invoice_type='purchase').select_related('person')
        
        # Apply filters
        month = filters.get('month')
        year = filters.get('year')
        search = filters.get('search', '').strip()
        
        if month and month != "All":
            try:
                month_num = MONTH_NAMES.index(month) + 1
                qs = qs.filter(date__month=month_num)
            except ValueError:
                pass
        
        if year:
            try:
                qs = qs.filter(date__year=int(year))
            except (ValueError, TypeError):
                pass
        
        if search:
            qs = qs.filter(person__name__icontains=search)
        
        if filters.get('person_id'):
            qs = qs.filter(person_id=filters['person_id'])
        
        if filters.get('date_from'):
            qs = qs.filter(date__gte=filters['date_from'])
        
        if filters.get('date_to'):
            qs = qs.filter(date__lte=filters['date_to'])
        
        if filters.get('is_paid') in ['true', '1']:
            qs = qs.filter(is_paid=True)
        elif filters.get('is_paid') in ['false', '0']:
            qs = qs.filter(is_paid=False)
        
        qs = qs.order_by('-date')
        
        # ✅ OPTIMIZED: Single aggregate query
        totals = qs.aggregate(
            total_amount=Sum('amount'),
            total_pending=Sum('amount', filter=F('is_paid')==False)
        )
        
        # ✅ Count AFTER aggregation
        total_count = qs.count()
        
        # Pagination
        paginated = self.paginate_queryset(qs, request)
        data_list = self.get_serializer(paginated, many=True).data
        
        data = {
            'total_amount': float(totals['total_amount'] or 0),
            'total_pending': float(totals['total_pending'] or 0),
            'count': total_count,
            'filters_applied': {
                'month': month,
                'year': year,
                'search': search,
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
        
        qs = Invoice.objects.filter(invoice_type='sale').select_related('person')
        
        # Apply filters (same as purchase)
        month = filters.get('month')
        year = filters.get('year')
        search = filters.get('search', '').strip()
        
        if month and month != "All":
            try:
                month_num = MONTH_NAMES.index(month) + 1
                qs = qs.filter(date__month=month_num)
            except ValueError:
                pass
        
        if year:
            try:
                qs = qs.filter(date__year=int(year))
            except (ValueError, TypeError):
                pass
        
        if search:
            qs = qs.filter(person__name__icontains=search)
        
        if filters.get('person_id'):
            qs = qs.filter(person_id=filters['person_id'])
        
        if filters.get('date_from'):
            qs = qs.filter(date__gte=filters['date_from'])
        
        if filters.get('date_to'):
            qs = qs.filter(date__lte=filters['date_to'])
        
        if filters.get('is_paid') in ['true', '1']:
            qs = qs.filter(is_paid=True)
        elif filters.get('is_paid') in ['false', '0']:
            qs = qs.filter(is_paid=False)
        
        qs = qs.order_by('-date')
        
        # ✅ OPTIMIZED: Single aggregate query
        totals = qs.aggregate(
            total_amount=Sum('amount'),
            total_pending=Sum('amount', filter=F('is_paid')==False)
        )
        
        total_count = qs.count()
        
        paginated = self.paginate_queryset(qs, request)
        data_list = self.get_serializer(paginated, many=True).data
        
        data = {
            'total_amount': float(totals['total_amount'] or 0),
            'total_pending': float(totals['total_pending'] or 0),
            'count': total_count,
            'filters_applied': {
                'month': month,
                'year': year,
                'search': search,
            },
            'results': data_list,
        }

        cache.set(cache_key, data, 300)
        print(f"📦 Sell: {len(data_list)}/{total_count} items")
        return Response(data)

    # ---------------------------------------------------
    # CRUD with Cache Invalidation
    # ---------------------------------------------------
    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.clear_cache()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        cache_key = self.get_cache_key("retrieve", pk)

        cached = cache.get(cache_key)
        if cached:
            print("🟢 Redis HIT (retrieve)")
            return Response(cached)

        print("🔴 Redis MISS (retrieve)")
        response = super().retrieve(request, pk)
        cache.set(cache_key, response.data, 300)
        return response

    def update(self, request, pk=None, partial=False):
        response = super().update(request, pk, partial=partial)
        self.clear_cache()
        return response

    def destroy(self, request, pk=None):
        response = super().destroy(request, pk)
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

    @action(detail=False, methods=['get'], url_path='person-names')
    def person_names(self, request):
        cache_key = self.get_cache_key("person_names")

        cached = cache.get(cache_key)
        if cached:
            return Response(cached)

        # ✅ OPTIMIZED: Use values_list instead of loading full objects
        names = list(Person.objects.values_list('name', flat=True))
        data = {"person_names": names}

        cache.set(cache_key, data, 600)
        return Response(data)

    # ---------------------------------------------------
    # DASHBOARD (OPTIMIZED)
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

        month = filters.get('month')
        year = filters.get('year')
        search = filters.get('search', '').strip()

        qs = Invoice.objects.select_related('person')

        if month and month != "All":
            try:
                month_num = MONTH_NAMES.index(month) + 1
                qs = qs.filter(date__month=month_num)
            except ValueError:
                pass

        if year:
            try:
                qs = qs.filter(date__year=int(year))
            except (ValueError, TypeError):
                pass

        if search:
            qs = qs.filter(person__name__icontains=search)

        # ✅ OPTIMIZED: Single aggregate for totals
        totals = qs.aggregate(
            total_purchase=Sum('amount', filter=F('invoice_type')=='purchase'),
            total_sales=Sum('amount', filter=F('invoice_type')=='sale')
        )

        # ✅ Recent 5 (limited query)
        recent_purchases = self.get_serializer(
            qs.filter(invoice_type='purchase').order_by('-date')[:5], many=True
        ).data

        recent_sales = self.get_serializer(
            qs.filter(invoice_type='sale').order_by('-date')[:5], many=True
        ).data

        # ✅ OPTIMIZED: Efficient pending calculation
        pending_purchases = qs.filter(
            invoice_type='purchase', 
            is_paid=False
        ).order_by('-date')[:5].values(
            'person__name', 'amount', 'person__role'
        )

        pending_sales = qs.filter(
            invoice_type='sale', 
            is_paid=False
        ).order_by('-date')[:5].values(
            'person__name', 'amount', 'person__role'
        )

        pending = []
        for inv in pending_purchases:
            pending.append({
                "person__name": inv['person__name'],
                "invoice_count": 1,
                "total_amount": float(inv['amount']),
                "invoice_type": "purchase",
                "role": inv['person__role']
            })

        for inv in pending_sales:
            pending.append({
                "person__name": inv['person__name'],
                "invoice_count": 1,
                "total_amount": float(inv['amount']),
                "invoice_type": "sale",
                "role": inv['person__role']
            })

        data = {
            "total_purchase": float(totals['total_purchase'] or 0),
            "total_sales": float(totals['total_sales'] or 0),
            "recent_purchases": recent_purchases,
            "recent_sales": recent_sales,
            "pending": pending,
        }

        cache.set(cache_key, data, 300)
        print(f"✅ Dashboard loaded successfully")
        return Response(data)
