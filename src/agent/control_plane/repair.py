from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping, Sequence
from typing import Any, Callable, Literal

from pydantic import Field

from agent.control_plane.capabilities import (
    AuthorityMetric,
    CapabilityPrimitive,
    CapabilityRegistry,
    CapabilityRisk,
    MetricDirection,
)
from agent.discovery.publication import (
    BusinessCompletionDecision,
    verify_business_completion_issuance,
)
from agent.discovery.production_authority import (
    DurableAuthorityLedger,
    repair_completion_context_digest,
    repair_completion_context_token,
    sha256_digest,
)
from agent.models import JsonModel


RepairDecisionKind = Literal["approve", "degrade", "reject"]
AuthorityMetricReader = Callable[[AuthorityMetric, str], int | float | bool]

_RISK_ORDER: dict[CapabilityRisk, int] = {
    "read_only": 0,
    "bounded_write": 1,
    "expensive": 2,
}

_FORBIDDEN_PARAMETER_KEYS = frozenset(
    {
        "callable",
        "code",
        "command",
        "executable",
        "metric_code",
        "python",
        "shell",
        "success_code",
    }
)


class SuccessMetricSpec(JsonModel):
    metric_id: str = Field(min_length=1, max_length=120)
    expected_delta_direction: MetricDirection
    aggregation: str | None = None
    source: str | None = None


class RepairProposal(JsonModel):
    """Open v2 proposal envelope; intent is not a closed business action kind."""

    schema_version: str = "discovery-repair-proposal/v2"
    proposal_id: str | None = None
    intent: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=4000)
    requested_capabilities: list[str] = Field(min_length=1, max_length=20)
    parameters: dict[str, Any] = Field(default_factory=dict)
    success_metric_spec: SuccessMetricSpec
    risk_class: CapabilityRisk


class RepairAuthorityDecision(JsonModel):
    decision: RepairDecisionKind
    reason_code: str
    reason: str
    requested_capabilities: list[str] = Field(default_factory=list)
    approved_capabilities: list[str] = Field(default_factory=list)
    rejected_capabilities: list[str] = Field(default_factory=list)
    metric_id: str | None = None
    expected_delta_direction: MetricDirection | None = None
    risk_class: CapabilityRisk | None = None
    parameter_hash: str | None = None
    idempotency_key: str | None = None
    max_refresh_attempts: int | None = None


class AuthorityMetricObservation(JsonModel):
    """Metric value captured from the registered source by the Authority Plane."""

    schema_version: Literal["authority-metric-observation/v1"] = (
        "authority-metric-observation/v1"
    )
    observation_id: str = Field(min_length=1, max_length=160)
    captured_by: Literal["repair_authority"]
    metric_id: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=300)
    aggregation: str = Field(min_length=1, max_length=120)
    scope_fingerprint: str = Field(min_length=1, max_length=300)
    value: int | float | bool
    issuance_token: str | None = None


class RepairAttemptResult(JsonModel):
    signature: str
    metric_id: str
    pre: int | float | bool | None = None
    post: int | float | bool | None = None
    delta: int | float | None = None
    progressed: bool = False
    no_progress_count: int = Field(default=0, ge=0)
    stop: bool = False
    reason_code: str
    events: list[str] = Field(default_factory=list)


class RepairAuthority:
    """Thin authority seam for proposal admission, delta, and honest events."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        no_progress_limit: int = 2,
        metric_reader: AuthorityMetricReader | None = None,
        ledger: DurableAuthorityLedger | None = None,
        authority_id: str | None = None,
    ) -> None:
        if no_progress_limit < 1:
            raise ValueError("no_progress_limit must be at least 1")
        self.registry = registry
        self.no_progress_limit = int(no_progress_limit)
        self._metric_reader = metric_reader
        self._ledger = ledger
        self.authority_id = str(authority_id or "").strip() or (
            "repair-authority:" + secrets.token_urlsafe(24)
        )
        self._last_no_progress_signature: str | None = None
        self._consecutive_no_progress = 0
        self._metric_issuance_ledger: dict[str, str] = {}
        self._executed_idempotency_keys: set[str] = set()
        self._pending_completion_attempts: dict[str, str] = {}
        self._consumed_completion_tokens: set[str] = set()

    def completion_context(self, attempt_id: str) -> dict[str, str]:
        """Create the recipient/attempt binding used by publication issuance."""

        normalized = str(attempt_id or "").strip()
        if not normalized:
            raise ValueError("completion attempt_id must not be empty")
        if normalized in self._pending_completion_attempts:
            raise ValueError("completion attempt_id is already pending")
        nonce = "repair-attempt-nonce:" + secrets.token_urlsafe(32)
        if self._ledger is not None:
            context_token = repair_completion_context_token(
                self.authority_id, normalized
            )
            context_digest = repair_completion_context_digest(
                self.authority_id,
                normalized,
                nonce,
            )
            if not self._ledger.reserve(
                "repair_completion_context",
                context_token,
                context_digest,
                binding={
                    "authority_id": self.authority_id,
                    "attempt_id": normalized,
                    "nonce": nonce,
                },
            ):
                raise ValueError("completion attempt_id was already issued")
        self._pending_completion_attempts[normalized] = nonce
        return {
            "repair_authority_id": self.authority_id,
            "repair_attempt_id": normalized,
            "repair_attempt_nonce": nonce,
        }

    def capture_metric_observation(
        self,
        *,
        metric_id: str,
        scope_fingerprint: str,
        observation_id: str | None = None,
    ) -> AuthorityMetricObservation:
        """Capture and register one value from an Authority metric reader seam."""

        metric = self.registry.metric(metric_id)
        if metric is None:
            raise ValueError(f"unregistered authority metric: {metric_id}")
        if self._metric_reader is None:
            raise RuntimeError("Authority metric reader is not configured")
        value = self._metric_reader(metric, scope_fingerprint)
        if _metric_value(value) is None:
            raise ValueError("authority metric value must be finite and computable")
        token = "authority-observation:" + secrets.token_urlsafe(24)
        observation = AuthorityMetricObservation(
            observation_id=observation_id or token,
            captured_by="repair_authority",
            metric_id=metric.metric_id,
            source=metric.source,
            aggregation=metric.aggregation,
            scope_fingerprint=scope_fingerprint,
            value=value,
            issuance_token=token,
        )
        digest = _metric_observation_digest(observation)
        if self._ledger is not None and not self._ledger.reserve(
            "metric_observation",
            token,
            digest,
            binding={
                "authority_id": self.authority_id,
                "metric_id": metric.metric_id,
                "scope_fingerprint": scope_fingerprint,
            },
        ):
            raise RuntimeError("Authority metric observation token collision")
        self._metric_issuance_ledger[token] = digest
        return observation

    def mark_execution_started(self, decision: RepairAuthorityDecision) -> None:
        """Reserve an Authority-issued operation key before dispatch."""

        if decision.decision != "approve" or not decision.idempotency_key:
            raise ValueError("only an approved decision can reserve execution")
        if decision.idempotency_key in self._executed_idempotency_keys:
            raise ValueError("duplicate idempotent execution")
        if self._ledger is not None and not self._ledger.reserve(
            "repair_idempotency",
            decision.idempotency_key,
            _json_payload_digest(decision.model_dump(mode="json")),
            binding={"authority_id": self.authority_id},
        ):
            raise ValueError("duplicate idempotent execution")
        self._executed_idempotency_keys.add(decision.idempotency_key)

    def review_proposal(
        self,
        proposal: RepairProposal | Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> RepairAuthorityDecision:
        raw = _mapping(proposal)
        context = context or {}
        requested = _string_list(raw.get("requested_capabilities"))

        unknown = [name for name in requested if self.registry.capability(name) is None]
        if unknown:
            return self._reject(
                "unknown_capability",
                "Proposal requests capability primitives that are not registered.",
                requested=requested,
                rejected=unknown,
            )

        metric_raw = _mapping(raw.get("success_metric_spec"))
        metric_id = str(metric_raw.get("metric_id") or "").strip()
        metric = self.registry.metric(metric_id)
        if metric is None:
            return self._reject(
                "uncomputable_metric",
                "success_metric_spec must reference an Authority Plane metric.",
                requested=requested,
                metric_id=metric_id or None,
            )
        if _metric_contains_executable_logic(metric_raw):
            return self._reject(
                "uncomputable_metric",
                "Model-supplied metric code or expressions are not executable.",
                requested=requested,
                metric_id=metric_id,
            )

        requested_aggregation = str(metric_raw.get("aggregation") or "").strip()
        requested_source = str(metric_raw.get("source") or "").strip()
        if requested_aggregation and requested_aggregation != metric.aggregation:
            return self._reject(
                "uncomputable_metric",
                "The requested aggregation does not match the metric whitelist.",
                requested=requested,
                metric_id=metric_id,
            )
        if requested_source and requested_source != metric.source:
            return self._reject(
                "uncomputable_metric",
                "The requested source does not match the authority-owned metric source.",
                requested=requested,
                metric_id=metric_id,
            )

        direction_raw = (
            metric_raw.get("expected_delta_direction")
            or raw.get("expected_delta_direction")
            or ""
        )
        direction = str(direction_raw).strip()
        if direction not in metric.directions:
            return self._reject(
                "invalid_metric_direction",
                "The requested comparison direction does not match the metric whitelist.",
                requested=requested,
                metric_id=metric_id,
            )

        if not requested:
            return self._reject(
                "missing_capability",
                "At least one registered capability primitive is required.",
                requested=requested,
                metric_id=metric_id,
            )

        primitives = [self.registry.capability(name) for name in requested]
        approved = [item for item in primitives if item is not None]
        if not any(metric_id in item.metric_ids for item in approved):
            return self._reject(
                "metric_not_supported_by_capabilities",
                "No requested capability can change the selected authority metric.",
                requested=requested,
                metric_id=metric_id,
            )

        issue_codes = list(
            dict.fromkeys(
                [
                    *_string_list(context.get("issue_code_set")),
                    *_string_list(context.get("issue_codes")),
                ]
            )
        )
        if not issue_codes:
            return self._reject(
                "missing_issue_context",
                "Active repair requires an Authority-issued audit issue set.",
                requested=requested,
                metric_id=metric_id,
            )
        policies = []
        for issue_code in issue_codes:
            policy = self.registry.issue_policy(issue_code)
            if policy is None:
                return self._reject(
                    "unknown_issue_policy",
                    f"No Authority admission policy is registered for issue: {issue_code}",
                    requested=requested,
                    metric_id=metric_id,
                )
            policies.append(policy)
        if policies:
            policy_capabilities = set().union(
                *(policy.capability_names for policy in policies)
            )
            incompatible = [
                name for name in requested if name not in policy_capabilities
            ]
            if incompatible:
                return self._reject(
                    "issue_policy_capability_denied",
                    "Requested capabilities fall outside the active issue policies.",
                    requested=requested,
                    rejected=incompatible,
                    metric_id=metric_id,
                )
            policy_metrics = set().union(
                *(policy.preferred_metric_ids for policy in policies)
            )
            if metric_id not in policy_metrics:
                return self._reject(
                    "issue_policy_metric_denied",
                    "The selected metric is not admitted for the active issue policies.",
                    requested=requested,
                    metric_id=metric_id,
                )
            available_scopes = set(
                _string_list(context.get("available_evidence_scopes"))
            )
            missing_scopes = sorted(
                {
                    policy.minimum_evidence_scope
                    for policy in policies
                    if policy.minimum_evidence_scope not in available_scopes
                }
            )
            if missing_scopes:
                return self._reject(
                    "issue_policy_evidence_scope_missing",
                    "Authority evidence scope is insufficient for the active issues: "
                    + ", ".join(missing_scopes),
                    requested=requested,
                    metric_id=metric_id,
                )

        parameters = _mapping(raw.get("parameters"))
        if not _is_json_value(parameters) or _contains_forbidden_parameter(parameters):
            return self._reject(
                "unsafe_parameters",
                "Capability parameters must be inert JSON data, not executable logic.",
                requested=requested,
                metric_id=metric_id,
            )
        if _requests_hard_constraint_bypass(parameters):
            return self._reject(
                "hard_constraint_violation",
                "A repair proposal cannot relax, override, or treat unknown hard constraints as passed.",
                requested=requested,
                metric_id=metric_id,
            )
        schema_error = self.registry.validate_parameters(requested, parameters)
        if schema_error:
            return self._reject(
                "parameter_schema_invalid",
                schema_error,
                requested=requested,
                metric_id=metric_id,
            )

        declared_risk = str(raw.get("risk_class") or "").strip()
        if declared_risk not in _RISK_ORDER:
            return self._reject(
                "invalid_risk_class",
                "Proposal risk_class is not registered.",
                requested=requested,
                metric_id=metric_id,
            )
        required_risk = max(approved, key=lambda item: _RISK_ORDER[item.risk_class]).risk_class
        if _RISK_ORDER[declared_risk] < _RISK_ORDER[required_risk]:
            return self._reject(
                "risk_understated",
                "Proposal risk_class is lower than its requested capabilities.",
                requested=requested,
                metric_id=metric_id,
            )
        if policies:
            risk_ceiling = min(
                (policy.risk_ceiling for policy in policies),
                key=lambda risk: _RISK_ORDER[risk],
            )
            if _RISK_ORDER[required_risk] > _RISK_ORDER[risk_ceiling]:
                return self._reject(
                    "issue_policy_risk_exceeded",
                    "Capability risk exceeds the active issue policy ceiling.",
                    requested=requested,
                    metric_id=metric_id,
                )

        budget_units = sum(item.budget_units for item in approved)
        remaining_tool_calls = _optional_nonnegative_int(context.get("remaining_tool_calls"))
        if remaining_tool_calls is not None and budget_units > remaining_tool_calls:
            return self._reject(
                "tool_budget_exhausted",
                "Remaining tool-call budget cannot cover the capability composition.",
                requested=requested,
                metric_id=metric_id,
            )
        if required_risk == "expensive":
            remaining_expensive = _optional_nonnegative_int(
                context.get("remaining_expensive_actions")
            )
            if remaining_expensive is not None and remaining_expensive < 1:
                return self._reject(
                    "expensive_budget_exhausted",
                    "No expensive-action budget remains.",
                    requested=requested,
                    metric_id=metric_id,
                )

        refresh = next(
            (item for item in approved if item.name == "refresh_auth_context"),
            None,
        )
        if refresh is not None:
            refresh_attempts = max(
                _optional_nonnegative_int(context.get("refresh_attempts")) or 0,
                _optional_nonnegative_int(context.get("auth_refresh_attempts")) or 0,
            )
            if refresh.max_attempts is not None and refresh_attempts >= refresh.max_attempts:
                return self._reject(
                    "refresh_limit_reached",
                    "Authority context refresh is bounded and has already been attempted.",
                    requested=requested,
                    metric_id=metric_id,
                )

        if "select_manifest" in requested and not _selection_is_build_ready(context):
            return self._reject(
                "build_ready_required",
                "Manifest selection cannot graduate a task before the publication contract is build-ready.",
                requested=requested,
                metric_id=metric_id,
            )

        try:
            normalized = RepairProposal.model_validate(
                {
                    **raw,
                    "success_metric_spec": {
                        "metric_id": metric_id,
                        "expected_delta_direction": direction,
                        "aggregation": requested_aggregation or None,
                        "source": requested_source or None,
                    },
                }
            )
        except (TypeError, ValueError) as exc:
            return self._reject(
                "invalid_proposal",
                f"Proposal envelope is invalid: {exc}",
                requested=requested,
                metric_id=metric_id,
            )

        parameter_hash = _parameter_hash(normalized.parameters)
        idempotency_key = _idempotency_key(
            approved,
            parameter_hash,
        )
        previous_keys = {
            *self._executed_idempotency_keys,
            *_string_list(context.get("executed_idempotency_keys")),
        }
        if (
            self._ledger is not None
            and self._ledger.get("repair_idempotency", idempotency_key) is not None
        ):
            previous_keys.add(idempotency_key)
        if idempotency_key in previous_keys:
            return self._reject(
                "duplicate_idempotent_execution",
                "An equivalent capability composition has already executed.",
                requested=requested,
                metric_id=metric_id,
            )

        return RepairAuthorityDecision(
            decision="approve",
            reason_code="authority_approved",
            reason="Registered capabilities, metric, risk, budget, and hard gates passed.",
            requested_capabilities=requested,
            approved_capabilities=requested,
            metric_id=metric_id,
            expected_delta_direction=normalized.success_metric_spec.expected_delta_direction,
            risk_class=required_risk,
            parameter_hash=parameter_hash,
            idempotency_key=idempotency_key,
            max_refresh_attempts=refresh.max_attempts if refresh is not None else None,
        )

    def record_attempt(
        self,
        attempt: Mapping[str, Any],
    ) -> RepairAttemptResult:
        metric_id = str(attempt.get("metric_id") or "").strip()
        metric = self.registry.metric(metric_id)
        signature = _attempt_signature(attempt, metric_id)
        if metric is None:
            self._reset_no_progress()
            return RepairAttemptResult(
                signature=signature,
                metric_id=metric_id,
                stop=True,
                reason_code="uncomputable_metric",
                events=["repair_attempt_finished", "repair_incomplete"],
            )

        observations = _trusted_metric_pair(
            attempt,
            metric,
            issuance_ledger=self._metric_issuance_ledger,
            durable_ledger=self._ledger,
            authority_id=self.authority_id,
        )
        if observations is None:
            return self._record_no_progress(
                signature=signature,
                metric_id=metric_id,
                pre=None,
                post=None,
                delta=None,
                first_reason="untrusted_metric_observation",
            )
        pre_observation, post_observation = observations
        pre = _metric_value(pre_observation.value)
        post = _metric_value(post_observation.value)
        if pre is None or post is None:
            return self._record_no_progress(
                signature=signature,
                metric_id=metric_id,
                pre=pre,
                post=post,
                delta=None,
                first_reason="metric_observation_invalid",
            )

        delta = _numeric(post) - _numeric(pre)
        direction = _attempt_direction(attempt, metric)
        progressed = _progressed(pre, post, direction)
        if progressed:
            self._reset_no_progress()
            return RepairAttemptResult(
                signature=signature,
                metric_id=metric_id,
                pre=pre,
                post=post,
                delta=delta,
                progressed=True,
                reason_code="metric_progressed",
                events=["repair_attempt_finished", "repair_progressed"],
            )

        return self._record_no_progress(
            signature=signature,
            metric_id=metric_id,
            pre=pre,
            post=post,
            delta=delta,
            first_reason="no_progress_observed",
        )

    def _record_no_progress(
        self,
        *,
        signature: str,
        metric_id: str,
        pre: int | float | bool | None,
        post: int | float | bool | None,
        delta: int | float | None,
        first_reason: str,
    ) -> RepairAttemptResult:
        if signature == self._last_no_progress_signature:
            self._consecutive_no_progress += 1
        else:
            self._last_no_progress_signature = signature
            self._consecutive_no_progress = 1
        stop = self._consecutive_no_progress >= self.no_progress_limit
        events = ["repair_attempt_finished", "repair_no_progress"]
        if stop:
            events.append("repair_incomplete")
        return RepairAttemptResult(
            signature=signature,
            metric_id=metric_id,
            pre=pre,
            post=post,
            delta=delta,
            progressed=False,
            no_progress_count=self._consecutive_no_progress,
            stop=stop,
            reason_code=(
                "no_progress_limit_reached" if stop else first_reason
            ),
            events=events,
        )

    def events_for_finished_attempt(
        self,
        *,
        attempt_event: str,
        audit_status: str,
        business_completion: Any,
        attempt_id: str | None = None,
    ) -> list[str]:
        """Classify terminal events without treating Runner return as success."""

        events = ["repair_attempt_finished"]
        if str(attempt_event or "").strip() != "repair_attempt_finished":
            events.append("repair_incomplete")
            return events
        audit_ready = str(audit_status or "").strip().casefold() == "ready"
        eligible = audit_ready and _business_completion_is_build_ready(
            business_completion,
            ledger=self._ledger,
        )
        if eligible and self._consume_completion_issuance(
            business_completion,
            attempt_id=attempt_id,
        ):
            events.extend(["repair_succeeded", "build_ready_succeeded"])
        else:
            events.append("repair_incomplete")
        return events

    def _consume_completion_issuance(
        self,
        decision: BusinessCompletionDecision,
        *,
        attempt_id: str | None,
    ) -> bool:
        token = str(decision.issuance_token or "")
        bound_attempt = str(attempt_id or decision.repair_attempt_id or "").strip()
        if self._ledger is not None:
            if (
                not token.startswith("durable-completion:")
                or decision.repair_authority_id != self.authority_id
                or decision.repair_attempt_id != bound_attempt
                or not bound_attempt
                or not decision.repair_attempt_nonce
            ):
                return False
            context_token = repair_completion_context_token(
                self.authority_id, bound_attempt
            )
            context_digest = repair_completion_context_digest(
                self.authority_id,
                bound_attempt,
                decision.repair_attempt_nonce,
            )
            if not self._ledger.verify(
                "repair_completion_context",
                context_token,
                context_digest,
                binding={
                    "authority_id": self.authority_id,
                    "attempt_id": bound_attempt,
                    "nonce": decision.repair_attempt_nonce,
                },
                allow_consumed=False,
            ):
                return False
            consumed = self._ledger.consume_many(
                [
                    ("business_completion", token, _business_completion_digest(decision)),
                    ("repair_completion_context", context_token, context_digest),
                ]
            )
            if consumed:
                self._pending_completion_attempts.pop(bound_attempt, None)
                self._consumed_completion_tokens.add(token)
            return consumed
        if (
            not token
            or token in self._consumed_completion_tokens
            or decision.repair_authority_id != self.authority_id
            or decision.repair_attempt_id != bound_attempt
            or bound_attempt not in self._pending_completion_attempts
            or decision.repair_attempt_nonce
            != self._pending_completion_attempts.get(bound_attempt)
        ):
            return False
        self._pending_completion_attempts.pop(bound_attempt, None)
        self._consumed_completion_tokens.add(token)
        return True

    def _reject(
        self,
        reason_code: str,
        reason: str,
        *,
        requested: list[str],
        rejected: list[str] | None = None,
        metric_id: str | None = None,
    ) -> RepairAuthorityDecision:
        return RepairAuthorityDecision(
            decision="reject",
            reason_code=reason_code,
            reason=reason,
            requested_capabilities=requested,
            rejected_capabilities=rejected or requested,
            metric_id=metric_id,
        )

    def _reset_no_progress(self) -> None:
        self._last_no_progress_signature = None
        self._consecutive_no_progress = 0


def upgrade_v1_repair_action(
    action: Mapping[str, Any] | Any,
    *,
    proposal_id: str | None = None,
) -> RepairProposal:
    """Replay a v1 action through explicit, conservative v2 semantics."""

    raw = _mapping(action)
    action_name = str(raw.get("action") or getattr(action, "action", "")).strip()
    mappings: dict[str, tuple[list[str], str, MetricDirection, CapabilityRisk]] = {
        "search_more": (["search_expand"], "unique_candidate_count", "increase", "expensive"),
        "inspect_candidates": (["inspect"], "reviewed_project_count", "increase", "read_only"),
        "rescore_projects": (
            ["recompute_validity"],
            "judgment_qualified_project_count",
            "increase",
            "bounded_write",
        ),
        "select_manifest": (["select_manifest"], "audit_ready", "false_to_true", "bounded_write"),
        "stop_with_limitations": (
            ["stop_with_limitations"],
            "audit_ready",
            "false_to_true",
            "read_only",
        ),
    }
    if action_name not in mappings:
        raise ValueError(f"unknown v1 repair action: {action_name or '<empty>'}")
    capabilities, metric_id, direction, risk = mappings[action_name]
    reason = str(raw.get("reason") or getattr(action, "reason", "") or action_name)
    parameters = {
        key: value
        for key, value in raw.items()
        if key in {"project_accessions", "constraint_ids"}
        and value
    }
    return RepairProposal(
        proposal_id=proposal_id,
        intent=f"Replay legacy repair action: {action_name}",
        rationale=reason,
        requested_capabilities=capabilities,
        parameters=parameters,
        success_metric_spec=SuccessMetricSpec(
            metric_id=metric_id,
            expected_delta_direction=direction,
        ),
        risk_class=risk,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _metric_contains_executable_logic(metric: Mapping[str, Any]) -> bool:
    allowed = {
        "aggregation",
        "expected_delta_direction",
        "metric_id",
        "source",
    }
    return bool(set(map(str, metric)).difference(allowed))


def _contains_forbidden_parameter(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().casefold()
            if normalized in _FORBIDDEN_PARAMETER_KEYS:
                return True
            if _contains_forbidden_parameter(item):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_forbidden_parameter(item) for item in value)
    return False


def _requests_hard_constraint_bypass(parameters: Mapping[str, Any]) -> bool:
    bypass_keys = {
        "allow_hard_unknown",
        "ignore_hard_conflicts",
        "override_hard_constraints",
        "relax_hard_constraints",
        "treat_hard_unknown_as_pass",
    }
    for key, value in parameters.items():
        if str(key).strip().casefold() in bypass_keys and bool(value):
            return True
        if isinstance(value, Mapping) and _requests_hard_constraint_bypass(value):
            return True
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if any(
                isinstance(item, Mapping) and _requests_hard_constraint_bypass(item)
                for item in value
            ):
                return True
    return False


def _is_json_value(value: Any) -> bool:
    try:
        json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError):
        return False
    return True


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _parameter_hash(parameters: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        parameters,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _idempotency_key(
    capabilities: list[CapabilityPrimitive],
    parameter_hash: str,
) -> str:
    payload = {
        "operations": sorted(
            (
                primitive.name,
                primitive.idempotency,
                parameter_hash if primitive.idempotency == "parameter_hash" else "",
            )
            for primitive in capabilities
        ),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _attempt_signature(attempt: Mapping[str, Any], metric_id: str) -> str:
    parameter_hash = str(attempt.get("parameter_hash") or "").strip()
    if not parameter_hash:
        parameter_hash = _parameter_hash(_mapping(attempt.get("parameters")))
    payload = {
        "approved_capability_set": sorted(
            set(_string_list(attempt.get("approved_capability_set")))
        ),
        "parameter_hash": parameter_hash,
        "issue_code_set": sorted(set(_string_list(attempt.get("issue_code_set")))),
        "metric_id": metric_id,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _metric_value(value: Any) -> int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, complex):
        if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
            return None
        return value
    return None


def _trusted_metric_pair(
    attempt: Mapping[str, Any],
    metric: AuthorityMetric,
    *,
    issuance_ledger: dict[str, str],
    durable_ledger: DurableAuthorityLedger | None = None,
    authority_id: str = "",
) -> tuple[AuthorityMetricObservation, AuthorityMetricObservation] | None:
    pre = attempt.get("pre_observation")
    post = attempt.get("post_observation")
    if not isinstance(pre, AuthorityMetricObservation) or not isinstance(
        post,
        AuthorityMetricObservation,
    ):
        return None
    for observation in (pre, post):
        token = str(observation.issuance_token or "")
        digest = _metric_observation_digest(observation)
        if (
            not token
            or observation.metric_id != metric.metric_id
            or observation.source != metric.source
            or observation.aggregation != metric.aggregation
        ):
            return None
        if durable_ledger is not None:
            if not durable_ledger.verify(
                "metric_observation",
                token,
                digest,
                binding={
                    "authority_id": authority_id,
                    "metric_id": metric.metric_id,
                    "scope_fingerprint": observation.scope_fingerprint,
                },
                allow_consumed=False,
            ):
                return None
        elif issuance_ledger.get(token) != digest:
            return None
    if pre.scope_fingerprint != post.scope_fingerprint:
        return None
    pre_token = str(pre.issuance_token or "")
    post_token = str(post.issuance_token or "")
    if pre_token == post_token:
        return None
    if durable_ledger is not None:
        if not durable_ledger.consume_many(
            [
                (
                    "metric_observation",
                    pre_token,
                    _metric_observation_digest(pre),
                ),
                (
                    "metric_observation",
                    post_token,
                    _metric_observation_digest(post),
                ),
            ]
        ):
            return None
    else:
        issuance_ledger.pop(pre_token, None)
        issuance_ledger.pop(post_token, None)
    return pre, post


def _metric_observation_digest(observation: AuthorityMetricObservation) -> str:
    payload = observation.model_dump(mode="json", exclude={"issuance_token"})
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _numeric(value: int | float | bool) -> int | float:
    return int(value) if isinstance(value, bool) else value


def _attempt_direction(
    attempt: Mapping[str, Any],
    metric: AuthorityMetric,
) -> MetricDirection:
    requested = str(attempt.get("expected_delta_direction") or "").strip()
    if requested in metric.directions:
        return requested  # type: ignore[return-value]
    if "false_to_true" in metric.directions:
        return "false_to_true"
    if "increase" in metric.directions:
        return "increase"
    return "decrease"


def _progressed(
    pre: int | float | bool,
    post: int | float | bool,
    direction: MetricDirection,
) -> bool:
    if direction == "false_to_true":
        return not bool(pre) and bool(post)
    if direction == "increase":
        return _numeric(post) > _numeric(pre)
    return _numeric(post) < _numeric(pre)


def _selection_is_build_ready(context: Mapping[str, Any]) -> bool:
    completion = context.get("business_completion") or context.get(
        "publication_decision"
    )
    if completion is None:
        return False
    return _business_completion_is_build_ready(completion)


def _business_completion_is_build_ready(
    value: Any,
    *,
    ledger: DurableAuthorityLedger | None = None,
) -> bool:
    if not isinstance(value, BusinessCompletionDecision):
        return False
    if not verify_business_completion_issuance(value, ledger=ledger):
        return False
    if value.authority_source != "publication_contract_registry":
        return False
    if not value.succeeded or value.status != "build_ready_succeeded":
        return False
    if value.package_kind != "build_ready" or not value.success_ui_allowed:
        return False
    package = value.build_ready_package
    if package is None:
        return False
    return (
        len(package.project_ids) > 0
        and len(package.files) > 0
        and value.progress.build_ready_projects == len(package.project_ids)
        and value.progress.build_ready_files == len(package.files)
    )


def _json_payload_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256_digest(encoded)


def _business_completion_digest(decision: BusinessCompletionDecision) -> str:
    return _json_payload_digest(
        decision.model_dump(mode="json", exclude={"issuance_token"})
    )


__all__ = [
    "RepairAttemptResult",
    "AuthorityMetricObservation",
    "AuthorityMetricReader",
    "RepairAuthority",
    "RepairAuthorityDecision",
    "RepairProposal",
    "SuccessMetricSpec",
    "upgrade_v1_repair_action",
]
