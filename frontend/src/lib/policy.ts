import { z } from 'zod';
import { ListSchema, RecordSchema } from './crm';

export const RequestDetailSchema = z.object({ record: RecordSchema, alternatives: z.array(RecordSchema) });
export const SingletonSchema = z.object({ record: z.object({ id: z.string() }).catchall(z.unknown()) });
export const ReplyPolicySchema = z.object({ record: z.object({ id: z.string(), enabled: z.boolean(), categories: z.array(z.string()), version: z.number() }).catchall(z.unknown()) });
export const ROUTINE_REPLY_CATEGORIES = ['welcome', 'acknowledgement', 'qualification', 'recommendation', 'faq_answer', 'clarification', 'availability', 'booking_confirmation', 'reminder', 'followup_routine'];
export const SuppressionSchema = z.object({ channel: z.string(), destination: z.string() }).catchall(z.unknown());
export const SuppressionResultSchema = z.object({ record: SuppressionSchema });
export const SuppressionListSchema = z.object({ items: z.array(SuppressionSchema), total: z.number().int().nonnegative(), page: z.number(), page_size: z.number() });
export const RequestListSchema = ListSchema;
export const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
export const REQUEST_STATUSES = ['proposed', 'awaiting_owner', 'approved', 'denied', 'expired', 'booked', 'cancelled'];
export const LINK_STATUSES = ['observed', 'active', 'revoked', 'superseded'];
export const TERMINAL_REQUEST = ['denied', 'expired', 'booked', 'cancelled'];
