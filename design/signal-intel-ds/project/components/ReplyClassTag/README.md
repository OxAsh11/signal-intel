# ReplyClassTag

Shows the class of a reply in the inbox and on the timeline.

- **Provide:** `replyClass`; `source="human"` adds a check, because a person's mark always beats an automatic one.
- Only Interested and Referral count as genuine replies. Referral is `signal`, never a muted rejection colour.
- Unsubscribe is `danger` because it triggers suppression across the whole organisation.
