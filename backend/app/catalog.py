"""Closed response repertoire for autonomous messaging (Phase D2, finding 1).

Automatically sent payloads contain zero model-generated free text by
construction: every emitted token is a backend-owned catalog string or a
server-rendered validated fact. New wording requires a catalog change,
review, and version bump. Arbitrary AI prose remains available only as
owner-reviewed material, never as an automatic-send payload.
"""
CATALOG_VERSION = 'PHRASEBOOK_V1'

# Categories the backend may auto-send. Anything else (including any
# model-invented label) forces rejection, then fallback + escalation.
AUTO_CATEGORIES = frozenset({
    'welcome', 'qualification', 'recommendation', 'faq',
    'clarification', 'availability', 'booking_confirmed', 'handoff', 'fallback',
})

# Conservative escalation tripwires on customer text. Scanning cannot
# certify safety, so these markers only ever force escalation upward:
# a marked turn persists no routine reply and moves to pending_owner.
SENSITIVE_MARKERS = (
    'discount', 'negotiat', 'lower the price', 'reduce the price', 'best price',
    'legal', 'contract', 'agreement', 'guarantee', 'refund', 'commission',
    'lawsuit', 'court', 'fraud', 'scam',
)

# Fixed fallback clarification: the only customer-visible text on the
# validation-failure path. Byte-exact in tests (D2-MSG-08).
FALLBACK_TEXT = ('I want to make sure I get this right. Could you rephrase that? '
                 'The owner can also review this chat and follow up.')

# Transitional glue may accompany any required category; every other
# segment must share the turn's backend-required category exactly.
TRANSITIONAL = 'transitional'

ENTRIES = {
    'ACK_WELCOME': dict(kind='phrase', category='welcome', slots={},
                        text='Thanks for reaching out to SoloEstate. I will help you find the right property.',
                        variants=['Welcome to SoloEstate. Tell me what kind of property you are looking for.']),
    'ACK_RECEIVED': dict(kind='phrase', category='welcome', slots={},
                         text='Got it. Let me look into that for you.',
                         variants=['Understood. Checking that for you now.']),
    'TRANS_LOOKUP': dict(kind='phrase', category=TRANSITIONAL, slots={},
                         text='Let me check our current listings for you.', variants=[]),
    'TRANS_SLOTS': dict(kind='phrase', category=TRANSITIONAL, slots={},
                        text='Let me check available visit times.', variants=[]),
    'Q_BUDGET': dict(kind='question', category='qualification', slots={},
                     text='What budget range are you considering? For example: 80L to 1.2Cr.',
                     variants=['Could you share your budget range, for example 80L to 1.2Cr?']),
    'Q_LOCALITY': dict(kind='question', category='qualification', slots={},
                       text='Which localities or areas do you prefer?',
                       variants=['Are there specific areas or localities you prefer?']),
    'Q_BHK': dict(kind='question', category='qualification', slots={},
                  text='How many bedrooms do you need?',
                  variants=['What size works for you: 1, 2, 3 or more bedrooms?']),
    'Q_TIMEFRAME': dict(kind='question', category='qualification', slots={},
                        text='When are you looking to decide or move?',
                        variants=['What is your timeline for deciding?']),
    'REC_MATCHES': dict(kind='phrase', category='recommendation', slots={'matches': 'match_list'},
                        text=('Based on your requirements, here are current matches:\n{matches}\n'
                              'Reply with any questions, or ask to schedule a visit.'),
                        variants=[]),
    'REC_NONE': dict(kind='phrase', category='recommendation', slots={},
                     text="I don't have a current match for those requirements. The owner will review and follow up.",
                     variants=[]),
    'FAQ_ANSWER': dict(kind='phrase', category='faq', slots={'quotes': 'faq_quotes'},
                       text='{quotes}', variants=[]),
    'Q_CLARIFY': dict(kind='question', category='clarification', slots={},
                      text="Could you tell me a little more about what you're looking for?",
                      variants=['Could you share a few more details so I can help better?']),
    'SLOT_OPTIONS': dict(kind='phrase', category='availability', slots={'slots': 'slot_list'},
                         text=('Available visit times for the next two days:\n{slots}\n'
                               'Reply with a time that suits you, or suggest another day.'),
                         variants=[]),
    'Q_CONFIRM_WINDOW': dict(kind='question', category='availability',
                             slots={'property': 'property', 'window': 'window'},
                             text=('Just to confirm: a visit to {property} on {window}? '
                                   'Please reply yes to confirm or suggest another time.'),
                             variants=[]),
    'BOOK_CONFIRMED': dict(kind='phrase', category='booking_confirmed',
                           slots={'property': 'property', 'window': 'window', 'ref': 'record'},
                           text='Your visit to {property} is confirmed for {window}. Reference: {ref}.',
                           variants=[]),
    'BOOK_EXCEPTION': dict(kind='phrase', category='availability', slots={},
                           text="That time needs owner approval. I've sent it for review and will confirm shortly.",
                           variants=[]),
    'HANDOFF_NOTICE': dict(kind='phrase', category='handoff', slots={},
                           text="I've asked the owner to review this personally and follow up with you.",
                           variants=[]),
    'FALLBACK_CLARIFY': dict(kind='phrase', category='fallback', slots={}, text=FALLBACK_TEXT, variants=[]),
}

# First-missing-field order maps backend readiness gaps to the one
# question the turn must ask; answered fields are never re-asked.
MISSING_TO_QUESTION = {
    'preferred_localities': 'Q_LOCALITY',
    'budget': 'Q_BUDGET',
    'bedrooms': 'Q_BHK',
    'purchase_timeframe': 'Q_TIMEFRAME',
}


def entry(entry_id):
    return ENTRIES.get(entry_id)


def render_text(entry_id, variant=0):
    """Server-side catalog string lookup; variant indexes alternate wording."""
    found = ENTRIES.get(entry_id)
    if found is None:
        return None
    if variant:
        options = found['variants']
        if variant < 1 or variant > len(options):
            return None
        return options[variant - 1]
    return found['text']
