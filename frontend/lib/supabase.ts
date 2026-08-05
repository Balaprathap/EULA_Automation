'use client';

import { createBrowserClient } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';

import { env } from './env';

let client: SupabaseClient | null = null;

/** Browser Supabase client. Uses the anon key only - never a service-role key. */
export function getSupabase(): SupabaseClient {
  if (!client) {
    // env.* throws a named error if a required variable is missing, so a
    // misconfigured deployment fails loudly instead of silently not signing in.
    client = createBrowserClient(env.supabaseUrl, env.supabaseAnonKey);
  }
  return client;
}
