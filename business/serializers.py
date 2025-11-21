from rest_framework import serializers
from django.db import transaction
from .models import Person, Invoice, InvoiceItem


class PersonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ['id', 'name', 'role']


class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = ['id', 'item_name', 'quantity', 'unit', 'price_per_unit', 'total']


class InvoiceSerializer(serializers.ModelSerializer):
    # For reading: show full person object
    person = PersonSerializer(read_only=True)
    
    # For writing: accept nested person data
    person_data = serializers.DictField(write_only=True, required=False)
    
    # For reading: show items
    items = InvoiceItemSerializer(many=True, read_only=True)
    
    # For writing: accept items data
    items_data = serializers.ListField(write_only=True, required=False)

    class Meta:
        model = Invoice
        fields = [
            'id', 'person', 'person_data', 'invoice_type', 'amount', 'date', 
            'is_paid', 'travel_text', 'additional_charge_percent', 
            'additional_charge_amount', 'transport_charge', 'subtotal', 
            'grand_total', 'pdf_file', 'items', 'items_data'
        ]

    @transaction.atomic
    def create(self, validated_data):
        # Extract write-only fields
        person_data = validated_data.pop('person_data', None)
        items_data = validated_data.pop('items_data', None)

        if not person_data:
            raise serializers.ValidationError({'person_data': 'This field is required.'})
        
        if not items_data:
            raise serializers.ValidationError({'items_data': 'This field is required.'})

        # Get or create person
        person, created = Person.objects.get_or_create(
            name=person_data['name'], 
            role=person_data['role']
        )

        # Create invoice
        invoice = Invoice.objects.create(person=person, **validated_data)

        # Create invoice items
        for item_data in items_data:
            InvoiceItem.objects.create(invoice=invoice, **item_data)

        return invoice

    @transaction.atomic
    def update(self, instance, validated_data):
        # Extract write-only fields
        person_data = validated_data.pop('person_data', None)
        items_data = validated_data.pop('items_data', None)

        # Update person if provided
        if person_data:
            person = instance.person
            person.name = person_data.get('name', person.name)
            person.role = person_data.get('role', person.role)
            person.save()

        # Update invoice fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Update items if provided
        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                InvoiceItem.objects.create(invoice=instance, **item_data)

        return instance
