import { z } from 'zod';

export const OUTBOX_STATUSES = ['queued', 'staged', 'submitting', 'sent', 'delivered', 'read', 'failed', 'unknown', 'dead', 'skipped'] as const;
export const OUTBOX_ACTIONS = ['mark_resolved_sent', 'authorize_resend', 'abandon'] as const;
export const OutboxRowSchema = z.object({ id: z.uuid() }).catchall(z.unknown());
export const OutboxListSchema = z.object({ items: z.array(OutboxRowSchema), total: z.number().int().nonnegative(), page: z.number(), page_size: z.number() });
export const OutboxResultSchema = z.object({ record: OutboxRowSchema }).catchall(z.unknown());
