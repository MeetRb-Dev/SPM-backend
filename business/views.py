from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Invoice, Person
from .serializers import InvoiceSerializer


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer
    
    # Built-in methods (already available):
    # - create() -> POST /api/invoices/
    # - retrieve() -> GET /api/invoices/{id}/
    # - update() -> PUT /api/invoices/{id}/
    # - partial_update() -> PATCH /api/invoices/{id}/
    # - destroy() -> DELETE /api/invoices/{id}/
    # - list() -> GET /api/invoices/

    @action(detail=False, methods=['get'])
    def purchase(self, request):
        """Get all purchase invoices"""
        purchase_invoices = Invoice.objects.filter(invoice_type='purchase').order_by('-date')
        serializer = self.get_serializer(purchase_invoices, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def sell(self, request):
        """Get all sell invoices"""
        sell_invoices = Invoice.objects.filter(invoice_type='sale').order_by('-date')
        serializer = self.get_serializer(sell_invoices, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'], url_path='mark_all_paid/(?P<person_id>[^/.]+)')
    def mark_all_paid(self, request, person_id=None):
        try:
            person = Person.objects.get(id=person_id)
        except Person.DoesNotExist:
            return Response({'detail': 'Person not found.'}, status=status.HTTP_404_NOT_FOUND)
        
        invoices = self.queryset.filter(person=person)
        updated_count = invoices.update(is_paid=True)

        return Response({'detail': f'{updated_count} invoices marked as paid.'}, status=status.HTTP_200_OK)
