-- Entirely fictional Bengaluru demo. INR prices; fixed demo week: 2–8 November 2026.
-- Stable identifiers and timestamps; reruns preserve existing records and edits.
INSERT INTO soloestate.leads (id, name, email, preferred_localities, budget_min, budget_max, bedrooms, notes, created_at) VALUES
('10000000-0000-4000-8000-000000000001', 'Aarav Demo', 'aarav@example.invalid', ARRAY['Indiranagar','Whitefield'], 8000000, 12000000, 2, 'Fictional: ready to visit this demo week.', '2026-11-02T03:00:00Z'),
('10000000-0000-4000-8000-000000000002', 'Mira Demo', 'mira@example.invalid', ARRAY['Whitefield'], 11000000, 16000000, 3, 'Fictional: prioritizes a home office.', '2026-11-03T03:00:00Z'),
('10000000-0000-4000-8000-000000000003', 'Dev Demo', 'dev@example.invalid', ARRAY['Indiranagar'], 5000000, 6500000, 2, 'Fictional: no exact budget match; do not invent one.', '2026-11-04T03:00:00Z'),
('10000000-0000-4000-8000-000000000004', 'Tara Demo', 'tara@example.invalid', ARRAY['Jayanagar'], 8500000, 10500000, 2, 'Fictional: previous-week lead for date filtering.', '2026-10-28T03:00:00Z')
ON CONFLICT (id) DO NOTHING;

INSERT INTO soloestate.properties (id, title, locality, price, bedrooms, description, created_at) VALUES
('20000000-0000-4000-8000-000000000001', 'Demo Cedar Apartment', 'Indiranagar', 10500000, 2, 'Fictional two-bedroom apartment; no real listing.', '2026-10-26T03:00:00Z'),
('20000000-0000-4000-8000-000000000002', 'Demo Willow Residence', 'Whitefield', 14500000, 3, 'Fictional three-bedroom apartment; no real listing.', '2026-10-26T03:00:00Z'),
('20000000-0000-4000-8000-000000000003', 'Demo Maple Home', 'Jayanagar', 9500000, 2, 'Fictional two-bedroom apartment; no real listing.', '2026-10-26T03:00:00Z')
ON CONFLICT (id) DO NOTHING;

INSERT INTO soloestate.visit_slots (id, starts_at, ends_at) VALUES
('30000000-0000-4000-8000-000000000001', '2026-11-05T04:30:00Z', '2026-11-05T05:00:00Z'),
('30000000-0000-4000-8000-000000000002', '2026-11-05T05:00:00Z', '2026-11-05T05:30:00Z'),
('30000000-0000-4000-8000-000000000003', '2026-11-05T08:30:00Z', '2026-11-05T09:00:00Z'),
('30000000-0000-4000-8000-000000000004', '2026-11-06T04:30:00Z', '2026-11-06T05:00:00Z'),
('30000000-0000-4000-8000-000000000005', '2026-11-06T05:00:00Z', '2026-11-06T05:30:00Z'),
('30000000-0000-4000-8000-000000000006', '2026-11-06T08:30:00Z', '2026-11-06T09:00:00Z')
ON CONFLICT (id) DO NOTHING;
