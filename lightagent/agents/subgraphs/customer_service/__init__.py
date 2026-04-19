"""Customer-service subgraph (SPEC-SUBGRAPH-001).

Public entry points::

    from lightagent.agents.subgraphs.customer_service import (
        build_customer_service_subgraph,
        CUSTOMER_SERVICE_CATEGORIES,
        CustomerServiceError,
    )
"""

from __future__ import annotations

from lightagent.agents.subgraphs.customer_service.builder import (
    build_customer_service_subgraph,
)
from lightagent.agents.subgraphs.customer_service.classifier_node import (
    CUSTOMER_SERVICE_CATEGORIES,
    make_classifier_node,
)
from lightagent.agents.subgraphs.customer_service.escalation_node import (
    make_escalation_gate,
)
from lightagent.agents.subgraphs.customer_service.faq_retrieval_node import (
    make_faq_retrieval_node,
)
from lightagent.agents.subgraphs.customer_service.response_generator_node import (
    make_response_generator_node,
)
from lightagent.agents.subgraphs.customer_service.ticket_creator_node import (
    make_ticket_creator_node,
)
from lightagent.core.exceptions import LightAgentError


class CustomerServiceError(LightAgentError):
    """Raised when the customer_service subgraph encounters a terminal fault."""


__all__ = [
    "CUSTOMER_SERVICE_CATEGORIES",
    "CustomerServiceError",
    "build_customer_service_subgraph",
    "make_classifier_node",
    "make_escalation_gate",
    "make_faq_retrieval_node",
    "make_response_generator_node",
    "make_ticket_creator_node",
]
