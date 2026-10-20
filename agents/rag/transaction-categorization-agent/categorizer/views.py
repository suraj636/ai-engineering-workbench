from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .llm_provider.base import LLMError
from .schemas import CompanyContext, Transaction
from .serializers import CategorizeRequestSerializer
from .services.categorizer import CategorizerService
from .services.response_parser import ParseError

logger = logging.getLogger(__name__)


class CategorizeView(APIView):
    """POST /api/categorize/  — return a category suggestion for one transaction."""

    service_class = CategorizerService

    def post(self, request):
        serializer = CategorizeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "invalid_request", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        txn = Transaction(**data["transaction"])
        ctx = CompanyContext(**data["company_context"])

        try:
            result = self.service_class().categorize(txn, ctx)
        except LLMError as e:
            logger.error("LLM error during categorization: %s", e)
            return Response(
                {"error": "llm_unavailable", "message": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except ParseError as e:
            logger.error("Failed to parse LLM response: %s", e)
            return Response(
                {"error": "llm_bad_response", "message": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(result.model_dump(), status=status.HTTP_200_OK)


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok"})
