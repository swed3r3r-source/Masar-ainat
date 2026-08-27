"""تعدادات النظام: الأدوار، الحالات، الأنواع، الأسباب، التنبيهات.

كل تعداد هنا يُعكَس كنوع `TEXT` مع قيد `CHECK` في قاعدة البيانات (وليس `ENUM`
أصليًا) حتى تبقى إضافة قيمة جديدة عملية ترحيل بسيطة لا قفلًا على الجدول.
"""

from __future__ import annotations

from enum import StrEnum


# ============================================================ الأدوار ========

class Role(StrEnum):
    ADMIN = "ADMIN"
    CENTRAL_PLANNER = "CENTRAL_PLANNER"
    HUB_SUPERVISOR = "HUB_SUPERVISOR"
    DRIVER = "DRIVER"
    EXTERNAL_REQUESTER = "EXTERNAL_REQUESTER"
    CONTROL_TOWER = "CONTROL_TOWER"  # برج التحكم: مراجعة الطلبات الفورية وطنيًا
    AUDITOR = "AUDITOR"              # قراءة فقط + سجل التدقيق
    INTEGRATION = "INTEGRATION"      # حساب خدمة لواجهة API الخارجية


BUILTIN_ROLES = frozenset(Role)

#: نطاق كل دور — يحدد أي مفاتيح نطاق إلزامية عند إنشاء المستخدم
ROLE_SCOPE: dict[str, str] = {
    Role.ADMIN: "GLOBAL",
    Role.CENTRAL_PLANNER: "GLOBAL",
    Role.CONTROL_TOWER: "GLOBAL",
    Role.AUDITOR: "GLOBAL",
    Role.INTEGRATION: "GLOBAL",
    Role.HUB_SUPERVISOR: "HUB",
    Role.DRIVER: "DRIVER",
    Role.EXTERNAL_REQUESTER: "FACILITY",
}


# ================================================== أنواع الجهات والخدمات ====

class FacilityType(StrEnum):
    HEALTH_CENTER = "HEALTH_CENTER"       # مركز صحي
    HOSPITAL = "HOSPITAL"                 # مستشفى
    LABORATORY = "LABORATORY"             # مختبر
    BLOOD_BANK = "BLOOD_BANK"             # بنك دم
    WAREHOUSE = "WAREHOUSE"               # مستودع
    CLINIC = "CLINIC"                     # عيادة
    OTHER = "OTHER"                       # جهة أخرى


#: فئة التجانس المستخدمة في قيد منع الخلط HC-13.
#: الجهات التي تشترك في الفئة يمكن جمعها على السائق نفسه.
FACILITY_MIXING_CLASS: dict[str, str] = {
    FacilityType.HEALTH_CENTER: "PRIMARY_CARE",
    FacilityType.CLINIC: "PRIMARY_CARE",
    FacilityType.HOSPITAL: "SECONDARY_CARE",
    FacilityType.LABORATORY: "LAB",
    FacilityType.BLOOD_BANK: "BLOOD",
    FacilityType.WAREHOUSE: "LOGISTICS",
    FacilityType.OTHER: "OTHER",
}


class ServiceType(StrEnum):
    ROUTINE = "ROUTINE"       # نقل روتيني مجدول
    URGENT = "URGENT"         # عاجل
    STAT = "STAT"             # فوري حرج
    RETURN = "RETURN"         # إرجاع صناديق/مستلزمات


class RequestKind(StrEnum):
    SCHEDULED = "SCHEDULED"
    ON_DEMAND = "ON_DEMAND"


class TemperatureMode(StrEnum):
    AMBIENT = "AMBIENT"       # حرارة الغرفة
    CHILLED = "CHILLED"       # مبرّد ٢–٨
    FROZEN = "FROZEN"         # مجمّد
    DEEP_FROZEN = "DEEP_FROZEN"
    CONTROLLED = "CONTROLLED" # نطاق مخصص


# =========================================================== الحالات =========

class ShipmentStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"        # طلب فوري بانتظار برج التحكم
    REJECTED = "REJECTED"
    PENDING_ASSIGNMENT = "PENDING_ASSIGNMENT"
    PLANNED = "PLANNED"
    ASSIGNED = "ASSIGNED"
    PUBLISHED = "PUBLISHED"
    IN_PROGRESS = "IN_PROGRESS"
    ARRIVED_PICKUP = "ARRIVED_PICKUP"
    PICKED_UP = "PICKED_UP"
    ARRIVED_DELIVERY = "ARRIVED_DELIVERY"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    CANCELLED_BEFORE_PICKUP = "CANCELLED_BEFORE_PICKUP"
    EXCEPTION = "EXCEPTION"
    FAILED = "FAILED"
    UNPLANNABLE = "UNPLANNABLE"


#: حالات تُعد «محسومة» — لا يمكن إكمال الرحلة قبل أن تصل كل شحناتها إليها
TERMINAL_SHIPMENT_STATUSES = frozenset({
    ShipmentStatus.COMPLETED,
    ShipmentStatus.DELIVERED,
    ShipmentStatus.CANCELLED_BEFORE_PICKUP,
    ShipmentStatus.FAILED,
    ShipmentStatus.REJECTED,
})

#: حالات تعني أن الشحنة في عهدة السائق
IN_CUSTODY_STATUSES = frozenset({
    ShipmentStatus.PICKED_UP,
    ShipmentStatus.ARRIVED_DELIVERY,
})


class RouteStatus(StrEnum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    ASSIGNED = "ASSIGNED"
    PUBLISHED = "PUBLISHED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"              # مسودة محفوظة بشكل دائم
    OPTIMIZING = "OPTIMIZING"
    OPTIMIZED = "OPTIMIZED"
    APPROVED = "APPROVED"
    DISPATCHED = "DISPATCHED"    # أُرسلت لمراكز الانطلاق
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


class ImportStatus(StrEnum):
    UPLOADED = "UPLOADED"
    MAPPING = "MAPPING"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    PARTIALLY_VALID = "PARTIALLY_VALID"
    REJECTED = "REJECTED"
    COMMITTED = "COMMITTED"


class StopKind(StrEnum):
    HUB_START = "HUB_START"
    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"
    HUB_END = "HUB_END"


class StopStatus(StrEnum):
    PENDING = "PENDING"
    ARRIVED = "ARRIVED"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


# ============================================== أسباب الاستثناء والتعذر =====

class ExceptionReason(StrEnum):
    NO_SAMPLES = "NO_SAMPLES"                       # لا توجد عينات
    SAMPLES_NOT_READY = "SAMPLES_NOT_READY"         # العينات غير جاهزة
    FACILITY_CLOSED = "FACILITY_CLOSED"             # المركز مغلق
    NO_STAFF = "NO_STAFF"                           # لا يوجد موظف لتسليم العينات
    CANCELLED_BEFORE_PICKUP = "CANCELLED_BEFORE_PICKUP"
    PICKUP_DELAYED = "PICKUP_DELAYED"
    DELIVERY_DELAYED = "DELIVERY_DELAYED"
    TEMPERATURE_BREACH = "TEMPERATURE_BREACH"
    BOX_DAMAGED = "BOX_DAMAGED"                     # تلف أو مشكلة في الصندوق
    LOCATION_UNREACHABLE = "LOCATION_UNREACHABLE"   # تعذر الوصول للموقع
    VEHICLE_BREAKDOWN = "VEHICLE_BREAKDOWN"
    OTHER = "OTHER"


#: أسباب تتطلب إثباتًا مرفوعًا (صورة/مستند) قبل الحفظ
EXCEPTION_REQUIRES_PROOF = frozenset({
    ExceptionReason.FACILITY_CLOSED,
    ExceptionReason.BOX_DAMAGED,
    ExceptionReason.LOCATION_UNREACHABLE,
    ExceptionReason.NO_STAFF,
})

#: أسباب تُبقي التزام التسليم مفتوحًا (§19: التسليم النهائي ضروري)
EXCEPTION_KEEPS_OBLIGATION_OPEN = frozenset({
    ExceptionReason.DELIVERY_DELAYED,
    ExceptionReason.LOCATION_UNREACHABLE,
    ExceptionReason.NO_STAFF,
    ExceptionReason.VEHICLE_BREAKDOWN,
    ExceptionReason.BOX_DAMAGED,
})


class UnplannableReason(StrEnum):
    IMPOSSIBLE_PICKUP_WINDOW = "IMPOSSIBLE_PICKUP_WINDOW"
    IMPOSSIBLE_SLA = "IMPOSSIBLE_SLA"
    SLA_BEFORE_PICKUP_WINDOW = "SLA_BEFORE_PICKUP_WINDOW"
    OUTSIDE_WORKING_HOURS = "OUTSIDE_WORKING_HOURS"
    NO_FEASIBLE_DRIVER = "NO_FEASIBLE_DRIVER"
    SHIFT_LIMIT_EXCEEDED = "SHIFT_LIMIT_EXCEEDED"
    MIXING_CONSTRAINT = "MIXING_CONSTRAINT"
    LONG_HAUL_LIMIT = "LONG_HAUL_LIMIT"
    UNREACHABLE_LOCATION = "UNREACHABLE_LOCATION"
    MISSING_COORDINATES = "MISSING_COORDINATES"
    FACILITY_NOT_REGISTERED = "FACILITY_NOT_REGISTERED"
    NO_CAPACITY = "NO_CAPACITY"
    ROUTING_SERVICE_UNAVAILABLE = "ROUTING_SERVICE_UNAVAILABLE"


# =========================================================== التنبيهات =======

class AlertType(StrEnum):
    PICKUP_WINDOW_APPROACHING = "PICKUP_WINDOW_APPROACHING"
    PICKUP_LATE = "PICKUP_LATE"
    DELIVERY_LATE = "DELIVERY_LATE"
    SLA_AT_RISK = "SLA_AT_RISK"
    SLA_BREACHED = "SLA_BREACHED"
    REQUEST_CANCELLED = "REQUEST_CANCELLED"
    SAMPLES_NOT_READY = "SAMPLES_NOT_READY"
    PICKUP_FAILED = "PICKUP_FAILED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    TEMPERATURE_BREACH = "TEMPERATURE_BREACH"
    TRACKING_STALE = "TRACKING_STALE"
    PUBLISHED_ROUTE_MODIFIED = "PUBLISHED_ROUTE_MODIFIED"
    NEW_ON_DEMAND_REQUEST = "NEW_ON_DEMAND_REQUEST"
    ROUTE_WITHOUT_DRIVER = "ROUTE_WITHOUT_DRIVER"
    DRIVER_SHORTAGE = "DRIVER_SHORTAGE"
    ASSIGNMENT_CONFLICT = "ASSIGNMENT_CONFLICT"


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


ALERT_DEFAULT_SEVERITY: dict[str, str] = {
    AlertType.PICKUP_WINDOW_APPROACHING: Severity.INFO,
    AlertType.PICKUP_LATE: Severity.MEDIUM,
    AlertType.DELIVERY_LATE: Severity.HIGH,
    AlertType.SLA_AT_RISK: Severity.MEDIUM,
    AlertType.SLA_BREACHED: Severity.CRITICAL,
    AlertType.REQUEST_CANCELLED: Severity.LOW,
    AlertType.SAMPLES_NOT_READY: Severity.MEDIUM,
    AlertType.PICKUP_FAILED: Severity.HIGH,
    AlertType.DELIVERY_FAILED: Severity.CRITICAL,
    AlertType.TEMPERATURE_BREACH: Severity.CRITICAL,
    AlertType.TRACKING_STALE: Severity.MEDIUM,
    AlertType.PUBLISHED_ROUTE_MODIFIED: Severity.LOW,
    AlertType.NEW_ON_DEMAND_REQUEST: Severity.MEDIUM,
    AlertType.ROUTE_WITHOUT_DRIVER: Severity.HIGH,
    AlertType.DRIVER_SHORTAGE: Severity.HIGH,
    AlertType.ASSIGNMENT_CONFLICT: Severity.HIGH,
}


# ================================================== تحذيرات محرك التخطيط ====

class WarningType(StrEnum):
    SLA_TIGHT = "SLA_TIGHT"
    WINDOW_TIGHT = "WINDOW_TIGHT"
    LONG_WAIT = "LONG_WAIT"
    SHIFT_NEAR_LIMIT = "SHIFT_NEAR_LIMIT"
    LONG_HAUL_ROUTE = "LONG_HAUL_ROUTE"
    MIXED_FACILITY_EXEMPTION_USED = "MIXED_FACILITY_EXEMPTION_USED"
    ESTIMATED_TRAVEL_TIME = "ESTIMATED_TRAVEL_TIME"
    UNASSIGNED_ROUTE = "UNASSIGNED_ROUTE"
    UNPLANNABLE_SHIPMENT = "UNPLANNABLE_SHIPMENT"
    DRIVER_SHORTAGE = "DRIVER_SHORTAGE"
    UNBALANCED_WORKLOAD = "UNBALANCED_WORKLOAD"
    SECOND_PICKUP_ORDER_FORCED = "SECOND_PICKUP_ORDER_FORCED"


# ============================================================== أخرى ========

class TemperatureSource(StrEnum):
    SENSOR = "SENSOR"
    GATEWAY = "GATEWAY"
    SIMULATION = "SIMULATION"   # يُوسم دائمًا ولا يُعرض كتكامل حقيقي
    MANUAL_ADMIN = "MANUAL_ADMIN"


class TemperatureStatus(StrEnum):
    IN_RANGE = "IN_RANGE"
    BREACH_HIGH = "BREACH_HIGH"
    BREACH_LOW = "BREACH_LOW"
    NO_SENSOR = "NO_SENSOR"
    STALE = "STALE"


class AuditAction(StrEnum):
    USER_CREATE = "USER_CREATE"
    USER_UPDATE = "USER_UPDATE"
    USER_DISABLE = "USER_DISABLE"
    ROLE_CHANGE = "ROLE_CHANGE"
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILURE = "LOGIN_FAILURE"
    LOGOUT = "LOGOUT"
    MASTER_DATA_CREATE = "MASTER_DATA_CREATE"
    MASTER_DATA_UPDATE = "MASTER_DATA_UPDATE"
    MASTER_DATA_VOID = "MASTER_DATA_VOID"
    SETTING_CHANGE = "SETTING_CHANGE"
    SCHEDULE_UPLOAD = "SCHEDULE_UPLOAD"
    SCHEDULE_COMMIT = "SCHEDULE_COMMIT"
    OPTIMIZER_RUN = "OPTIMIZER_RUN"
    PLAN_APPROVE = "PLAN_APPROVE"
    PLAN_DISPATCH = "PLAN_DISPATCH"
    ROUTE_ASSIGN = "ROUTE_ASSIGN"
    ROUTE_UNASSIGN = "ROUTE_UNASSIGN"
    ROUTE_PUBLISH = "ROUTE_PUBLISH"
    ROUTE_MODIFY_PUBLISHED = "ROUTE_MODIFY_PUBLISHED"
    SHIPMENT_STATUS_CHANGE = "SHIPMENT_STATUS_CHANGE"
    SHIPMENT_CANCEL = "SHIPMENT_CANCEL"
    EXCEPTION_RECORD = "EXCEPTION_RECORD"
    EXCEPTION_ACTION = "EXCEPTION_ACTION"
    ON_DEMAND_APPROVE = "ON_DEMAND_APPROVE"
    ON_DEMAND_REJECT = "ON_DEMAND_REJECT"
    DOCUMENT_UPLOAD = "DOCUMENT_UPLOAD"
    DOCUMENT_ACCESS = "DOCUMENT_ACCESS"
    INTEGRATION_CHANGE = "INTEGRATION_CHANGE"
    DATA_EXPORT = "DATA_EXPORT"
    HARD_DELETE = "HARD_DELETE"


#: عمليات لا يجوز أن تمر دون سبب مكتوب
ACTIONS_REQUIRING_REASON = frozenset({
    AuditAction.ROUTE_MODIFY_PUBLISHED,
    AuditAction.ROUTE_UNASSIGN,
    AuditAction.SHIPMENT_CANCEL,
    AuditAction.EXCEPTION_ACTION,
    AuditAction.ON_DEMAND_REJECT,
    AuditAction.SETTING_CHANGE,
    AuditAction.HARD_DELETE,
    AuditAction.MASTER_DATA_VOID,
})
