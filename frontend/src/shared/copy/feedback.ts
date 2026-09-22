export const FEEDBACK_COPY = {
  prompt: 'Was this response helpful?',
  positive: 'Rate response helpful',
  negative: 'Rate response unhelpful',
  correction: 'What should the correct answer be? (optional)',
  comment: 'Additional comments (optional)',
  formLabel: 'Provide correction or comment',
  cancel: 'Cancel',
  submit: 'Submit Feedback',
  submitted: '✓ Feedback submitted',
  errors: {
    connection: 'Connection unavailable. Please try again.',
    unavailable: 'Feedback is currently unavailable.',
    unauthorized: 'You are not allowed to rate this message.',
    invalid: 'That feedback could not be accepted.',
    inactive: 'This conversation is no longer active.',
  },
} as const