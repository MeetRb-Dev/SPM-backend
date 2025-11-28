from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.cache import cache
from .models import Invoice, Person
from .serializers import InvoiceSerializer
import hashlib

class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer

    def get_cache_key(self, action_name, filters=None, pk=None):
        """Simple cache keys"""
        filter_str = str(sorted(filters.items())) if filters else ""
        key_parts = [action_name, filter_str]
        if pk:
            key_parts.append(str(pk))
        key_hash = hashlib.md5(".".join(key_parts).encode()).hexdigest()
        return f"rksuppliers:invoice:{key_hash}"

    def _get_cached_list(self, queryset, filters):
        """Cache list operations"""
        key = self.get_cache_key("list", filters)
        data = cache.get(key)
        if data is None:
            print(f"🔴 Cache MISS - Querying DB")  # Debug
            serializer = self.get_serializer(queryset, many=True)
            data = serializer.data
            cache.set(key, data, 300)
            print(f"✅ Cache SET - {len(data)} invoices")
        else:
            print(f"🟢 Cache HIT - {len(data)} invoices")
        return data

    def _invalidate_cache(self):
        """Clear all invoice cache"""
        cache.delete_pattern("rksuppliers:invoice:*")
        print("🗑️ Cache CLEARED")

    def list(self, request):
        filters = request.query_params.dict()
        queryset = self.filter_queryset(self.get_queryset())
        data = self._get_cached_list(queryset, filters)
        return Response(data)

    @action(detail=False, methods=['get'])
    def purchase(self, request):
        queryset = self.get_queryset().filter(invoice_type='purchase').order_by('-date')
        data = self._get_cached_list(queryset, {'type': 'purchase'})
        return Response(data)

    @action(detail=False, methods=['get'])
    def sell(self, request):
        queryset = self.get_queryset().filter(invoice_type='sale').order_by('-date')
        data = self._get_cached_list(queryset, {'type': 'sale'})
        return Response(data)

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        self._invalidate_cache()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        key = self.get_cache_key("retrieve", pk=pk)
        data = cache.get(key)
        if data is None:
            response = super().retrieve(request, pk)
            cache.set(key, response.data, 180)
            return response
        return Response(data)

    def update(self, request, pk=None):
        response = super().update(request, pk)
        self._invalidate_cache()
        return response

    def destroy(self, request, pk=None):
        response = super().destroy(request, pk)
        self._invalidate_cache()
        return response

    @action(detail=False, methods=['post'], url_path='mark_all_paid/(?P<person_id>[^/.]+)')
    def mark_all_paid(self, request, person_id=None):
        try:
            person = Person.objects.get(id=person_id)
        except Person.DoesNotExist:
            return Response({'detail': 'Person not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        invoices = self.queryset.filter(person=person)
        updated_count = invoices.update(is_paid=True)
        self._invalidate_cache()
        return Response({'detail': f'{updated_count} invoices marked as paid.'})
