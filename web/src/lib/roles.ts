import type { Role } from "@/lib/api";

export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  manager: "Manager",
  team_member: "Team member",
};

export function roleLabel(role: Role): string {
  return ROLE_LABELS[role] ?? role;
}
