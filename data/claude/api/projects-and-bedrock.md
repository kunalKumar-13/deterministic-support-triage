# Claude on AWS Bedrock — common integration issues

Claude is available on AWS Bedrock as a managed model. Common reasons
for failing requests:

- **Wrong region**: Bedrock model availability varies by region. Verify
  the model ID is enabled in your region in the Bedrock console.
- **Model not requested**: many Bedrock accounts must explicitly
  request access to specific Anthropic models before they can be
  invoked. Confirm under **Bedrock > Model access**.
- **IAM permissions**: the calling principal needs
  `bedrock:InvokeModel` on the specific model ARN.
- **Rate limits**: Bedrock's per-account TPM/RPM limits are separate
  from the limits on api.anthropic.com.
- **Body shape**: Bedrock uses the Anthropic messages payload but
  expects it nested under a Bedrock-specific envelope. Confirm against
  the current Bedrock documentation.

If you see a `ValidationException` or `AccessDeniedException`, check
permissions and the model-access toggle first. If you see
`ThrottlingException`, lower your concurrency or request a quota
increase from AWS.

## When to contact Anthropic vs AWS

- **Model behavior / quality** issues: contact Anthropic.
- **Bedrock access / permissions / throttling** issues: contact AWS.
- **Both providers' availability** in an outage: check status pages of
  both providers before opening a case.
