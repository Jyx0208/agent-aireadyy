from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping


MetricDirection = Literal["increase", "decrease", "false_to_true"]
CapabilityRisk = Literal["read_only", "bounded_write", "expensive"]
EvidenceScope = Literal["project", "assay", "file", "spectrum", "sample", "portfolio"]


@dataclass(frozen=True, slots=True)
class AuthorityMetric:
    """A metric that the Authority Plane can compute from trusted state."""

    metric_id: str
    source: str
    aggregation: str
    directions: frozenset[MetricDirection]

    @property
    def default_direction(self) -> MetricDirection:
        return sorted(self.directions)[0]


@dataclass(frozen=True, slots=True)
class CapabilityPrimitive:
    """An additive, authority-approved execution primitive.

    The registry describes execution boundaries. It intentionally does not
    encode domain-specific scientific routes or repair intent names.
    """

    name: str
    risk_class: CapabilityRisk
    metric_ids: frozenset[str]
    parameter_schema: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    budget_units: int = 1
    idempotency: str = "parameter_hash"
    adapter: str = "unbound"
    authority_events: tuple[str, ...] = (
        "repair_attempt_started",
        "repair_attempt_finished",
    )
    requires_fresh_context: bool = False
    max_attempts: int | None = None


@dataclass(frozen=True, slots=True)
class IssueCapabilityPolicy:
    """LP6 admission guidance without prescribing an execution sequence."""

    capability_names: frozenset[str]
    preferred_metric_ids: frozenset[str]
    risk_ceiling: CapabilityRisk
    minimum_evidence_scope: EvidenceScope = "project"


class CapabilityRegistry:
    """Additive registry for capability primitives and authority metrics."""

    def __init__(
        self,
        *,
        capabilities: Mapping[str, CapabilityPrimitive] | None = None,
        metrics: Mapping[str, AuthorityMetric] | None = None,
        issue_policies: Mapping[str, IssueCapabilityPolicy] | None = None,
    ) -> None:
        self._capabilities = dict(capabilities or {})
        self._metrics = dict(metrics or {})
        self._issue_policies = dict(issue_policies or {})

    @classmethod
    def default(cls) -> "CapabilityRegistry":
        metrics = _default_metrics()
        capabilities = _default_capabilities()
        registry = cls(
            capabilities=capabilities,
            metrics=metrics,
            issue_policies=_default_issue_policies(),
        )
        registry._validate_references()
        return registry

    @property
    def capabilities(self) -> Mapping[str, CapabilityPrimitive]:
        return MappingProxyType(self._capabilities)

    @property
    def metrics(self) -> Mapping[str, AuthorityMetric]:
        return MappingProxyType(self._metrics)

    @property
    def issue_policies(self) -> Mapping[str, IssueCapabilityPolicy]:
        return MappingProxyType(self._issue_policies)

    def capability(self, name: str) -> CapabilityPrimitive | None:
        return self._capabilities.get(str(name or "").strip())

    def metric(self, metric_id: str) -> AuthorityMetric | None:
        return self._metrics.get(str(metric_id or "").strip())

    def issue_policy(self, issue_code: str) -> IssueCapabilityPolicy | None:
        return self._issue_policies.get(str(issue_code or "").strip())

    def validate_parameters(
        self,
        capability_names: list[str],
        parameters: Mapping[str, object],
    ) -> str | None:
        """Validate a flat composition payload against registered schemas."""

        allowed: dict[str, frozenset[str]] = {}
        for name in capability_names:
            primitive = self.capability(name)
            if primitive is None:
                return f"unregistered capability: {name}"
            for key, types in primitive.parameter_schema.items():
                allowed[key] = allowed.get(key, frozenset()) | types
        for key, value in parameters.items():
            if key not in allowed:
                return f"unexpected parameter: {key}"
            actual = _json_type(value)
            if actual not in allowed[key]:
                expected = ",".join(sorted(allowed[key]))
                return f"invalid type for {key}: expected {expected}, got {actual}"
            if key == "max_items" and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                return "max_items must be a positive integer"
            if key in {
                "blocker_codes",
                "constraint_ids",
                "membership_refs",
                "observation_ids",
                "project_accessions",
                "project_ids",
                "queries",
                "source_refs",
            } and not _nonempty_string_array(value):
                return f"{key} must contain non-empty strings"
            if actual == "string" and not str(value).strip():
                return f"{key} must not be empty"
        return None

    def register_capability(self, primitive: CapabilityPrimitive) -> None:
        if not primitive.name.strip():
            raise ValueError("capability name must not be empty")
        if primitive.name in self._capabilities:
            raise ValueError(f"capability already registered: {primitive.name}")
        unknown_metrics = primitive.metric_ids.difference(self._metrics)
        if unknown_metrics:
            raise ValueError(
                "capability references unregistered metrics: "
                + ", ".join(sorted(unknown_metrics))
            )
        self._capabilities[primitive.name] = primitive

    def register_metric(self, metric: AuthorityMetric) -> None:
        if not metric.metric_id.strip():
            raise ValueError("metric_id must not be empty")
        if metric.metric_id in self._metrics:
            raise ValueError(f"metric already registered: {metric.metric_id}")
        self._metrics[metric.metric_id] = metric

    def _validate_references(self) -> None:
        for primitive in self._capabilities.values():
            unknown_metrics = primitive.metric_ids.difference(self._metrics)
            if unknown_metrics:
                raise ValueError(
                    f"{primitive.name} references unregistered metrics: "
                    + ", ".join(sorted(unknown_metrics))
                )
        for issue_code, policy in self._issue_policies.items():
            unknown_capabilities = policy.capability_names.difference(
                self._capabilities
            )
            unknown_metrics = policy.preferred_metric_ids.difference(self._metrics)
            if unknown_capabilities or unknown_metrics:
                raise ValueError(
                    f"{issue_code} references unregistered authority entries: "
                    + ", ".join(sorted(unknown_capabilities | unknown_metrics))
                )


def _metric(
    metric_id: str,
    source: str,
    aggregation: str,
    *directions: MetricDirection,
) -> AuthorityMetric:
    return AuthorityMetric(
        metric_id=metric_id,
        source=source,
        aggregation=aggregation,
        directions=frozenset(directions),
    )


def _default_metrics() -> dict[str, AuthorityMetric]:
    """LP2 metric whitelist, mirrored from WAVE2_ARTIFACTS.md."""

    rows = (
        _metric(
            "unique_candidate_count",
            "candidate_manifest.project_id",
            "count_distinct",
            "increase",
        ),
        _metric(
            "reviewed_project_count",
            "inspection.successful_project_id",
            "count_distinct",
            "increase",
        ),
        _metric(
            "judgment_qualified_project_count",
            "inspection_backed_judgment.project_id",
            "count_distinct",
            "increase",
        ),
        _metric(
            "verified_observation_count",
            "evidence_store.observation_id",
            "count_distinct",
            "increase",
        ),
        _metric(
            "unresolved_claim_count",
            "quality_audit.unresolved_claim_id",
            "count_distinct",
            "decrease",
        ),
        _metric(
            "missing_build_ready_field_count",
            "publication_contract.missing_field",
            "count_distinct",
            "decrease",
        ),
        _metric(
            "hard_conflict_count",
            "constraint_audit.hard_conflict_id",
            "count_distinct",
            "decrease",
        ),
        _metric(
            "hard_unknown_count",
            "constraint_audit.hard_unknown_id",
            "count_distinct",
            "decrease",
        ),
        _metric(
            "build_ready_project_count",
            "publication_contract.build_ready_project_id",
            "count_distinct",
            "increase",
        ),
        _metric(
            "build_ready_file_count",
            "build_ready_package.file_id",
            "count_distinct",
            "increase",
        ),
        _metric(
            "active_context_freshness",
            "authority_context.active_context_freshness",
            "boolean",
            "increase",
            "false_to_true",
        ),
        _metric(
            "audit_ready",
            "quality_audit.ready",
            "boolean",
            "increase",
            "false_to_true",
        ),
    )
    return {row.metric_id: row for row in rows}


def _primitive(
    name: str,
    risk_class: CapabilityRisk,
    metric_ids: set[str],
    **kwargs: object,
) -> CapabilityPrimitive:
    return CapabilityPrimitive(
        name=name,
        risk_class=risk_class,
        metric_ids=frozenset(metric_ids),
        **kwargs,
    )


def _schema(**fields: str | tuple[str, ...]) -> Mapping[str, frozenset[str]]:
    return MappingProxyType(
        {
            key: frozenset((value,) if isinstance(value, str) else value)
            for key, value in fields.items()
        }
    )


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return "unsupported"


def _nonempty_string_array(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def _default_capabilities() -> dict[str, CapabilityPrimitive]:
    rows = (
        _primitive(
            "search_expand",
            "expensive",
            {"unique_candidate_count"},
            parameter_schema=_schema(
                filters="object",
                max_items="integer",
                queries="array",
                query="string",
                strategy="string",
                target_group="string",
            ),
            adapter="discovery.search_expand",
        ),
        _primitive(
            "inspect",
            "read_only",
            {
                "reviewed_project_count",
                "judgment_qualified_project_count",
                "verified_observation_count",
                "unresolved_claim_count",
                "missing_build_ready_field_count",
                "hard_conflict_count",
                "hard_unknown_count",
                "build_ready_project_count",
                "build_ready_file_count",
            },
            parameter_schema=_schema(
                constraint_ids="array",
                max_items="integer",
                project_accessions="array",
                project_ids="array",
                retry_operation="string",
                stale_context_id="string",
                stale_grant_id="string",
                target_group="string",
            ),
            adapter="discovery.inspect",
            requires_fresh_context=True,
        ),
        _primitive(
            "materialize_evidence",
            "bounded_write",
            {
                "verified_observation_count",
                "unresolved_claim_count",
                "missing_build_ready_field_count",
                "hard_conflict_count",
                "hard_unknown_count",
                "build_ready_project_count",
                "build_ready_file_count",
            },
            parameter_schema=_schema(
                constraint_ids="array",
                max_items="integer",
                membership_refs="array",
                observation_ids="array",
                project_accessions="array",
                project_ids="array",
                source_refs="array",
                target_group="string",
            ),
            adapter="evidence.materialize",
        ),
        _primitive(
            "recompute_validity",
            "bounded_write",
            {
                "judgment_qualified_project_count",
                "unresolved_claim_count",
                "missing_build_ready_field_count",
                "hard_conflict_count",
                "hard_unknown_count",
                "build_ready_project_count",
                "build_ready_file_count",
                "audit_ready",
            },
            parameter_schema=_schema(
                constraint_ids="array",
                max_items="integer",
                project_accessions="array",
                project_ids="array",
                target_group="string",
            ),
            adapter="discovery.recompute_validity",
        ),
        _primitive(
            "refresh_auth_context",
            "bounded_write",
            {"active_context_freshness"},
            parameter_schema=_schema(
                retry_operation="string",
                stale_context_id="string",
                stale_grant_id="string",
            ),
            adapter="authority.refresh_context",
            max_attempts=1,
        ),
        _primitive(
            "select_manifest",
            "bounded_write",
            {"build_ready_project_count", "build_ready_file_count", "audit_ready"},
            parameter_schema=_schema(
                manifest_path="string",
                manifest_ref="string",
                package_id="string",
            ),
            adapter="discovery.select_manifest",
        ),
        _primitive(
            "stop_with_limitations",
            "read_only",
            {"audit_ready"},
            parameter_schema=_schema(
                blocker_codes="array",
                limitations="array",
                reason="string",
            ),
            budget_units=0,
            adapter="authority.stop_with_limitations",
        ),
        _primitive(
            "ask_user_blocking_question",
            "read_only",
            {"audit_ready"},
            parameter_schema=_schema(
                blocker_codes="array",
                question="string",
                reason="string",
            ),
            budget_units=0,
            adapter="authority.ask_user_blocking_question",
        ),
    )
    return {row.name: row for row in rows}


def _default_issue_policies() -> dict[str, IssueCapabilityPolicy]:
    """LP6 issue mapping from WAVE2_ARTIFACTS.md.

    These are admission bounds and metric defaults. They are not a workflow:
    the Agent remains free to combine registered primitives and replan.
    """

    rows: dict[str, tuple[set[str], set[str], CapabilityRisk]] = {
        "candidate_manifest_missing": (
            {"search_expand", "stop_with_limitations"},
            {"unique_candidate_count"},
            "expensive",
        ),
        "quality_target_not_reached": (
            {"search_expand", "inspect", "stop_with_limitations"},
            {"judgment_qualified_project_count"},
            "expensive",
        ),
        "quality_target_shortfall_at_stop": (
            {"search_expand", "inspect", "stop_with_limitations"},
            {"judgment_qualified_project_count"},
            "expensive",
        ),
        "portfolio_search_not_converged": (
            {"search_expand", "stop_with_limitations"},
            {"unique_candidate_count"},
            "expensive",
        ),
        "portfolio_search_stopped_before_convergence": (
            {"search_expand", "stop_with_limitations"},
            {"unique_candidate_count"},
            "expensive",
        ),
        "high_relevance_inspection_coverage_incomplete": (
            {"inspect", "refresh_auth_context"},
            {"reviewed_project_count"},
            "expensive",
        ),
        "candidate_inspections_failed": (
            {"inspect", "refresh_auth_context"},
            {"reviewed_project_count"},
            "expensive",
        ),
        "inspected_projects_missing_judgments": (
            {"inspect", "materialize_evidence"},
            {"unresolved_claim_count"},
            "bounded_write",
        ),
        "qualified_projects_have_unresolved_constraints": (
            {"inspect", "materialize_evidence", "recompute_validity"},
            {"hard_unknown_count"},
            "bounded_write",
        ),
        "constraint_assessment_evidence_invalid": (
            {"inspect", "materialize_evidence"},
            {"verified_observation_count"},
            "bounded_write",
        ),
        "qualified_project_has_no_inspected_files": (
            {"inspect", "materialize_evidence", "recompute_validity"},
            {"missing_build_ready_field_count"},
            "bounded_write",
        ),
        "qualified_project_has_no_delivery_assets": (
            {"inspect", "materialize_evidence", "recompute_validity"},
            {"missing_build_ready_field_count"},
            "bounded_write",
        ),
        "qualified_project_still_needs_review": (
            {"inspect", "materialize_evidence", "recompute_validity"},
            {"build_ready_file_count"},
            "bounded_write",
        ),
        "delivery_relies_on_weak_keep_files": (
            {"inspect", "materialize_evidence", "recompute_validity"},
            {"build_ready_file_count"},
            "bounded_write",
        ),
        "hard_builtin_constraint_not_met": (
            {
                "inspect",
                "materialize_evidence",
                "ask_user_blocking_question",
                "stop_with_limitations",
            },
            {"hard_conflict_count", "hard_unknown_count"},
            "bounded_write",
        ),
        "hard_per_project_min_files_not_met": (
            {
                "inspect",
                "materialize_evidence",
                "ask_user_blocking_question",
                "stop_with_limitations",
            },
            {"hard_conflict_count", "hard_unknown_count"},
            "bounded_write",
        ),
        "hard_per_project_min_samples_not_met": (
            {
                "inspect",
                "materialize_evidence",
                "ask_user_blocking_question",
                "stop_with_limitations",
            },
            {"hard_conflict_count", "hard_unknown_count"},
            "bounded_write",
        ),
        "hard_portfolio_constraint_not_met": (
            {
                "inspect",
                "materialize_evidence",
                "ask_user_blocking_question",
                "stop_with_limitations",
            },
            {"hard_conflict_count", "hard_unknown_count"},
            "bounded_write",
        ),
        "preview_coverage_not_backed_by_selection": (
            {"inspect", "recompute_validity"},
            {"unresolved_claim_count"},
            "bounded_write",
        ),
        "selected_manifest_contains_non_delivery_files": (
            {"recompute_validity", "select_manifest"},
            {"build_ready_file_count"},
            "bounded_write",
        ),
        "selected_manifest_contains_unqualified_projects": (
            {"recompute_validity", "select_manifest"},
            {"build_ready_file_count"},
            "bounded_write",
        ),
        "selected_manifest_missing": (
            {"recompute_validity", "stop_with_limitations"},
            {"audit_ready"},
            "bounded_write",
        ),
        "stale_context": (
            {"refresh_auth_context", "inspect"},
            {"active_context_freshness"},
            "bounded_write",
        ),
        "autonomous_repair_ceiling_exhausted": (
            {"ask_user_blocking_question", "stop_with_limitations"},
            {"audit_ready"},
            "read_only",
        ),
        "portfolio_search_stopped_at_hard_ceiling": (
            {"ask_user_blocking_question", "stop_with_limitations"},
            {"audit_ready"},
            "read_only",
        ),
        "quality_audit_policy_denied": (
            {"ask_user_blocking_question", "stop_with_limitations"},
            {"audit_ready"},
            "read_only",
        ),
    }
    return {
        issue_code: IssueCapabilityPolicy(
            capability_names=frozenset(capability_names),
            preferred_metric_ids=frozenset(metric_ids),
            risk_ceiling=risk_ceiling,
            minimum_evidence_scope=_minimum_evidence_scope(issue_code),
        )
        for issue_code, (capability_names, metric_ids, risk_ceiling) in rows.items()
    }


def _minimum_evidence_scope(issue_code: str) -> EvidenceScope:
    file_scope_issues = {
        "delivery_relies_on_weak_keep_files",
        "qualified_project_has_no_delivery_assets",
        "qualified_project_has_no_inspected_files",
        "qualified_project_still_needs_review",
        "selected_manifest_contains_non_delivery_files",
    }
    portfolio_scope_issues = {
        "autonomous_repair_ceiling_exhausted",
        "portfolio_search_not_converged",
        "portfolio_search_stopped_at_hard_ceiling",
        "portfolio_search_stopped_before_convergence",
        "quality_audit_policy_denied",
    }
    if issue_code in file_scope_issues:
        return "file"
    if issue_code in portfolio_scope_issues:
        return "portfolio"
    return "project"


__all__ = [
    "AuthorityMetric",
    "CapabilityPrimitive",
    "CapabilityRegistry",
    "CapabilityRisk",
    "EvidenceScope",
    "IssueCapabilityPolicy",
    "MetricDirection",
]
