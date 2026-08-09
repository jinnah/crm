"use client";

import { useAuth } from "@/components/auth-context";
import { roleLabel } from "@/lib/roles";

export default function HomePage() {
  const { user } = useAuth();
  return (
    <section>
      <h1>Welcome</h1>
      <p>
        Signed in as <strong>{user.email}</strong> ({roleLabel(user.role)}).
      </p>
      <p>CRM features arrive in later phases. Use the navigation above to manage your account.</p>
    </section>
  );
}
