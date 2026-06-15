import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  Position,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery } from "@/api/auth/useQuery";
import { getUserPermissions } from "@/api/auth/users";
import { listGroupRoles, listGroupMembers } from "@/api/auth/groups";
import { listPermissions } from "@/api/server/permissions";
import { ApiError } from "@/api/client";
import type {
  GroupMember,
  ServiceName,
  UserPermissionsResponse,
} from "@/api/auth/types";
import type { GroupRoleAssignment } from "@/api/auth/groups";
import type { PermissionListResponse } from "@/api/server/types";

/**
 * AccessGraph — живой граф доступа субъекта (пользователь или группа).
 *
 * Слои слева направо:
 *   subject → [для user: группы] → service-роли (с источником direct/via-group)
 *   → сервисы → действия/ресурсы (там, где у сервиса есть матрица действий).
 *
 * Данные тянутся из auth_service (/users/{id}/permissions либо
 * /groups/{id}/roles) плюс, опционально, матрица действий server_service
 * (/permissions) для обогащения server-ролей. У account_admin и
 * платформенных loging_*-ролей effective-набор пуст — рисуем отдельный
 * platform_role-узел с пометкой про доступ через платформенную роль.
 */

export interface AccessGraphSubject {
  kind: "user" | "group";
  id: string;
}

interface Props {
  subject: AccessGraphSubject;
}

// Палитра по слою — держим в одном месте, чтобы легенда и узлы не разъезжались.
const LAYER = {
  subject: { bg: "#3b3663", border: "#6c63ff", label: "Субъект" },
  group: { bg: "#2d3a4f", border: "#5b8def", label: "Группа" },
  role: { bg: "#3a3320", border: "#d2a13a", label: "Service-роль" },
  service: { bg: "#1f3b32", border: "#3fae7a", label: "Сервис" },
  action: { bg: "#3a2330", border: "#c46089", label: "Действие / ресурс" },
  platform: { bg: "#3a1f1f", border: "#d2603a", label: "Платформенная роль" },
} as const;

type LayerKind = keyof typeof LAYER;

const COL_X: Record<LayerKind, number> = {
  subject: 0,
  group: 280,
  role: 560,
  service: 840,
  action: 1120,
  platform: 280,
};

const ROW_H = 70;

function nodeStyle(layer: LayerKind): React.CSSProperties {
  const c = LAYER[layer];
  return {
    background: c.bg,
    border: `1px solid ${c.border}`,
    borderRadius: 8,
    color: "#e6e6ef",
    fontSize: 12,
    padding: "8px 12px",
    width: 220,
  };
}

function mkNode(
  id: string,
  layer: LayerKind,
  label: React.ReactNode,
  row: number,
): Node {
  return {
    id,
    position: { x: COL_X[layer], y: row * ROW_H },
    data: { label },
    style: nodeStyle(layer),
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    draggable: true,
    connectable: false,
  };
}

function mkEdge(source: string, target: string, dimmed = false): Edge {
  return {
    id: `${source}->${target}`,
    source,
    target,
    animated: false,
    style: { stroke: dimmed ? "#555" : "#888", strokeWidth: 1.5 },
    markerEnd: { type: MarkerType.ArrowClosed, color: dimmed ? "#555" : "#888" },
  };
}

// server_service хранит каталог действий по ключу role→action на entity_type.
// Группируем по имени роли, чтобы развернуть server-роли в действия.
function actionsByServerRole(
  matrix: PermissionListResponse | undefined,
): Map<string, string[]> {
  const out = new Map<string, string[]>();
  if (!matrix) return out;
  for (const e of matrix.items) {
    const key = `${e.entity_type}.${e.action}`;
    const list = out.get(e.role) ?? [];
    if (!list.includes(key)) list.push(key);
    out.set(e.role, list);
  }
  return out;
}

// secret_service выражает права scope'ами, loging — admin/reader. У них нет
// action-матрицы, поэтому действия даём как статические метки по имени роли.
function staticServiceActions(
  service: ServiceName,
  role: string,
): string[] {
  if (service === "secret_service") {
    return ["scope: personal", "scope: department", "scope: cross_department"];
  }
  if (service === "loging_service") {
    if (role.includes("admin")) return ["rules", "retention", "read"];
    return ["read"];
  }
  return [];
}

interface BuiltGraph {
  nodes: Node[];
  edges: Edge[];
  empty: boolean;
  platformOnly: boolean;
}

function buildUserGraph(
  perms: UserPermissionsResponse,
  serverMatrix: PermissionListResponse | undefined,
): BuiltGraph {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const serverActions = actionsByServerRole(serverMatrix);

  const subjId = "subject";
  nodes.push(
    mkNode(
      subjId,
      "subject",
      <div>
        <div style={{ fontWeight: 600 }}>{perms.username}</div>
        <div style={{ opacity: 0.7, fontSize: 10 }}>
          {perms.department_name ?? "платформенный"}
        </div>
      </div>,
      0,
    ),
  );

  const hasEffective = Object.keys(perms.service_roles ?? {}).length > 0;

  // account_admin / loging_* — effective пуст, доступ идёт через платформенную
  // роль. Рисуем отдельный platform_role-узел.
  if (perms.platform_role && !hasEffective) {
    const pid = "platform-role";
    nodes.push(
      mkNode(
        pid,
        "platform",
        <div>
          <div style={{ fontWeight: 600 }}>{perms.platform_role}</div>
          <div style={{ opacity: 0.7, fontSize: 10 }}>
            effective пуст · доступ через платформенную роль
          </div>
        </div>,
        0,
      ),
    );
    edges.push(mkEdge(subjId, pid));
    return { nodes, edges, empty: false, platformOnly: true };
  }

  // Группы субъекта.
  const groupNodeById = new Map<string, string>();
  (perms.groups ?? []).forEach((g, i) => {
    const gid = `group-${g.group_id}`;
    groupNodeById.set(g.group_id, gid);
    nodes.push(
      mkNode(
        gid,
        "group",
        <div>
          <div style={{ fontWeight: 600 }}>{g.group_name}</div>
          <div style={{ opacity: 0.7, fontSize: 10 }}>{g.department_id}</div>
        </div>,
        i,
      ),
    );
    edges.push(mkEdge(subjId, gid));
  });

  // service-role узлы: ключ service+role, источник direct либо via-group.
  type RoleMeta = { service: ServiceName; role: string; viaGroups: string[]; direct: boolean };
  const roleMeta = new Map<string, RoleMeta>();
  const roleKey = (svc: string, role: string) => `${svc}::${role}`;

  for (const dr of perms.direct_service_roles ?? []) {
    const k = roleKey(dr.service_name, dr.role_name);
    const m = roleMeta.get(k) ?? {
      service: dr.service_name,
      role: dr.role_name,
      viaGroups: [],
      direct: false,
    };
    m.direct = true;
    roleMeta.set(k, m);
  }
  for (const g of perms.groups ?? []) {
    for (const sr of g.service_roles ?? []) {
      const k = roleKey(sr.service_name, sr.role_name);
      const m = roleMeta.get(k) ?? {
        service: sr.service_name,
        role: sr.role_name,
        viaGroups: [],
        direct: false,
      };
      if (!m.viaGroups.includes(g.group_id)) m.viaGroups.push(g.group_id);
      roleMeta.set(k, m);
    }
  }

  let roleRow = 0;
  const serviceNodeById = new Map<string, string>();
  let serviceRow = 0;
  let actionRow = 0;

  for (const [k, m] of roleMeta) {
    const rid = `role-${k}`;
    const srcLabel = m.direct
      ? "direct"
      : `via ${m.viaGroups.length} groups`;
    nodes.push(
      mkNode(
        rid,
        "role",
        <div>
          <div style={{ fontWeight: 600 }}>{m.role}</div>
          <div style={{ opacity: 0.7, fontSize: 10 }}>
            {m.service} · {srcLabel}
          </div>
        </div>,
        roleRow++,
      ),
    );

    // Подключаем роль к источнику: direct → subject, via-group → каждой группе.
    if (m.direct) edges.push(mkEdge(subjId, rid));
    for (const gId of m.viaGroups) {
      const gNode = groupNodeById.get(gId);
      if (gNode) edges.push(mkEdge(gNode, rid));
    }

    // Узел сервиса (один на сервис).
    let svcNode = serviceNodeById.get(m.service);
    if (!svcNode) {
      svcNode = `svc-${m.service}`;
      serviceNodeById.set(m.service, svcNode);
      nodes.push(mkNode(svcNode, "service", m.service, serviceRow++));
    }
    edges.push(mkEdge(rid, svcNode));

    // Действия/ресурсы — из server-матрицы либо статические метки.
    const actions =
      m.service === "server_service"
        ? serverActions.get(m.role) ?? []
        : staticServiceActions(m.service, m.role);
    for (const a of actions.slice(0, 12)) {
      const aid = `act-${m.service}-${m.role}-${a}`;
      if (!nodes.some((n) => n.id === aid)) {
        nodes.push(mkNode(aid, "action", a, actionRow++));
      }
      edges.push(mkEdge(svcNode, aid, true));
    }
  }

  const empty =
    (perms.groups ?? []).length === 0 &&
    roleMeta.size === 0 &&
    !perms.platform_role;

  return { nodes, edges, empty, platformOnly: false };
}

function buildGroupGraph(
  groupId: string,
  roles: GroupRoleAssignment[],
  members: GroupMember[],
  serverMatrix: PermissionListResponse | undefined,
): BuiltGraph {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const serverActions = actionsByServerRole(serverMatrix);

  const subjId = "subject";
  nodes.push(
    mkNode(
      subjId,
      "subject",
      <div>
        <div style={{ fontWeight: 600 }}>группа</div>
        <div style={{ opacity: 0.7, fontSize: 10 }}>
          {members.length} участников
        </div>
      </div>,
      0,
    ),
  );
  void groupId;

  let roleRow = 0;
  let serviceRow = 0;
  let actionRow = 0;
  const serviceNodeById = new Map<string, string>();

  for (const r of roles) {
    let svcNode = serviceNodeById.get(r.service_name);
    if (!svcNode) {
      svcNode = `svc-${r.service_name}`;
      serviceNodeById.set(r.service_name, svcNode);
      nodes.push(mkNode(svcNode, "service", r.service_name, serviceRow++));
    }
    for (const roleName of r.roles) {
      const rid = `role-${r.service_name}-${roleName}`;
      nodes.push(
        mkNode(
          rid,
          "role",
          <div>
            <div style={{ fontWeight: 600 }}>{roleName}</div>
            <div style={{ opacity: 0.7, fontSize: 10 }}>{r.service_name}</div>
          </div>,
          roleRow++,
        ),
      );
      edges.push(mkEdge(subjId, rid));
      edges.push(mkEdge(rid, svcNode));

      const actions =
        r.service_name === "server_service"
          ? serverActions.get(roleName) ?? []
          : staticServiceActions(r.service_name, roleName);
      for (const a of actions.slice(0, 12)) {
        const aid = `act-${r.service_name}-${roleName}-${a}`;
        if (!nodes.some((n) => n.id === aid)) {
          nodes.push(mkNode(aid, "action", a, actionRow++));
        }
        edges.push(mkEdge(svcNode, aid, true));
      }
    }
  }

  return { nodes, edges, empty: roles.length === 0, platformOnly: false };
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-3 mb-3 text-xs">
      {(Object.keys(LAYER) as LayerKind[]).map((k) => (
        <span key={k} className="flex items-center gap-1">
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 12,
              borderRadius: 3,
              background: LAYER[k].bg,
              border: `1px solid ${LAYER[k].border}`,
            }}
          />
          <span className="text-dim">{LAYER[k].label}</span>
        </span>
      ))}
    </div>
  );
}

export default function AccessGraph({ subject }: Props) {
  const permsQ = useQuery<UserPermissionsResponse>(
    () => getUserPermissions(subject.id),
    [subject.id],
    { enabled: subject.kind === "user" && !!subject.id },
  );
  const groupRolesQ = useQuery<GroupRoleAssignment[]>(
    () => listGroupRoles(subject.id),
    [subject.id],
    { enabled: subject.kind === "group" && !!subject.id },
  );
  const groupMembersQ = useQuery<GroupMember[]>(
    () => listGroupMembers(subject.id),
    [subject.id],
    { enabled: subject.kind === "group" && !!subject.id },
  );
  // Матрица действий server_service — обогащение, не блокер. Ошибку/пустоту
  // молча игнорируем (у вызывающего может не быть доступа к server-матрице).
  const serverMatrixQ = useQuery<PermissionListResponse>(
    () => listPermissions(),
    [],
    {},
  );

  const built = useMemo<BuiltGraph | null>(() => {
    if (subject.kind === "user") {
      if (!permsQ.data) return null;
      return buildUserGraph(permsQ.data, serverMatrixQ.data);
    }
    if (!groupRolesQ.data) return null;
    return buildGroupGraph(
      subject.id,
      groupRolesQ.data,
      groupMembersQ.data ?? [],
      serverMatrixQ.data,
    );
  }, [
    subject.kind,
    subject.id,
    permsQ.data,
    groupRolesQ.data,
    groupMembersQ.data,
    serverMatrixQ.data,
  ]);

  const loading =
    subject.kind === "user"
      ? permsQ.loading
      : groupRolesQ.loading || groupMembersQ.loading;
  const error =
    subject.kind === "user"
      ? permsQ.error
      : groupRolesQ.error || groupMembersQ.error;

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center p-10">
        <div className="text-sm text-dim">Загрузка графа доступа…</div>
      </div>
    );
  }

  if (error) {
    const msg =
      error instanceof ApiError && error.status === 403
        ? "Нет доступа к правам этого субъекта."
        : error instanceof ApiError && error.status === 404
          ? "Субъект не найден."
          : error.message;
    return (
      <div className="p-5">
        <div className="empty-card danger">{msg}</div>
      </div>
    );
  }

  if (!built || built.empty) {
    return (
      <div className="p-5">
        <div className="empty-card">
          У субъекта нет групп, ролей и платформенной роли — граф пуст.
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col p-5">
      <Legend />
      {built.platformOnly && (
        <div className="text-xs text-dim mb-2">
          Effective-набор пуст: доступ предоставляется платформенной ролью
          напрямую, без service-ролей.
        </div>
      )}
      <div
        className="border border-token rounded-lg"
        style={{ height: 520, minHeight: 320 }}
      >
        <ReactFlow
          nodes={built.nodes}
          edges={built.edges}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.2}
          proOptions={{ hideAttribution: true }}
          nodesConnectable={false}
          edgesFocusable={false}
        >
          <Background color="#333" gap={20} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}
