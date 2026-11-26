from rest_framework import serializers


class HealthSerializer(serializers.Serializer):
    postgres = serializers.CharField()
    redis = serializers.CharField()
