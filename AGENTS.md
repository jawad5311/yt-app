# Project Rules & Trello Tracking Guidelines

## Trello Testing Cards for Changes
For every feature, modification, bug fix, or update made to this codebase:
1. **Always Create/Update Trello Cards**:
   - Board: `Views App` (`6a87d7a1eb000a1dd43c705b`)
   - Target List: `Needs Testing` (`6aabc3024e8aa4f431a39045`)
2. **Granularity**:
   - Keep cards as separated as possible: create a distinct, standalone card for each individual feature, UI change, or functional update. Do not bundle multiple unrelated features into a single card.
3. **Card Content Structure**:
   - **Title**: `Test: <Feature / Update Name>`
   - **Overview**: Brief summary of the purpose and user-facing impact.
   - **Implementation Details**: Files modified, functions updated, and underlying mechanisms.
   - **What to Test & How to Test**: Step-by-step manual and automated verification instructions that the assistant can execute directly to validate the change.
4. **Reporting**:
   - In turn responses, always report the created card title, target list name, and direct Trello URL (`https://trello.com/c/...`).
