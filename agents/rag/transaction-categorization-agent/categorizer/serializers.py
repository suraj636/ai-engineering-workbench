from rest_framework import serializers


class HistoricalTxnSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=500)
    payee = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)
    category = serializers.CharField(max_length=200)


class CompanyContextSerializer(serializers.Serializer):
    company_id = serializers.CharField(max_length=100)
    industry = serializers.CharField(max_length=200)
    chart_of_accounts = serializers.ListField(
        child=serializers.CharField(max_length=200),
        min_length=1,
    )
    historical_transactions = HistoricalTxnSerializer(many=True, required=False, default=list)


class TransactionSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=500)
    payee = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)


class CategorizeRequestSerializer(serializers.Serializer):
    transaction = TransactionSerializer()
    company_context = CompanyContextSerializer()
