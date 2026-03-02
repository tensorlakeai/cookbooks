/**
 * Twilio Function to forward incoming message webhooks to a remote endpoint
 * with Bearer Token authentication.
 *
 * This function expects two Environment Variables to be set in your
 * Twilio Functions configuration:
 * 1. REMOTE_ENDPOINT_URL: The full URL of the remote server (e.g., https://api.example.com/webhook)
 * 2. AUTH_BEARER_TOKEN: The secret Bearer Token required by your endpoint.
 */

exports.handler = async function(context, event, callback) {
  console.log("Function triggered by incoming message:", event.MessageSid);
  console.log("Event keys:", Object.keys(event));
  console.log("NumMedia:", event.NumMedia);

  // 1. Get secrets from Environment Variables
  const remoteUrl = "https://api.tensorlake.ai/applications/handle_sms";
  const bearerToken = "tl_apiKey_wKb7zfMKjCdqkJgRrhMk6_s-w861Kr2_dexV7oRajFK5vqSph3et";

  if (!remoteUrl || !bearerToken) {
    const errorMsg = "Critical configuration error: REMOTE_ENDPOINT_URL or AUTH_BEARER_TOKEN is not set in Environment Variables.";
    console.error(errorMsg);
    return callback(new Error(errorMsg));
  }

  // 2. Extract media from the event
  const media = [];
  const numMedia = parseInt(event.NumMedia || '0');
  console.log("Parsed numMedia:", numMedia);

  for (let i = 0; i < numMedia; i++) {
    const mediaUrl = event[`MediaUrl${i}`];
    const contentType = event[`MediaContentType${i}`];
    console.log(`Media ${i}: url=${mediaUrl}, contentType=${contentType}`);
    if (mediaUrl) {
      media.push({ url: mediaUrl, contentType: contentType || 'application/octet-stream' });
    }
  }

  // 3. Build payload
  const payload = {
    messageSid: event.MessageSid,
    smsSid: event.SmsSid,
    accountSid: event.AccountSid,
    messagingServiceSid: event.MessagingServiceSid,
    from: event.From,
    to: event.To,
    body: event.Body,
    numMedia: event.NumMedia || '0',
    receivedAt: new Date().toISOString(),
    media: media
  };

  console.log("Payload media:", JSON.stringify(payload.media));

  try {
    const response = await fetch(remoteUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': `Bearer ${bearerToken}`
      },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      const errorBody = await response.text();
      const errorMsg = `Remote endpoint responded with status ${response.status}: ${errorBody}`;
      console.error(errorMsg);
      return callback(new Error(errorMsg));
    }

    const responseData = await response.json();
    console.log("Successfully forwarded webhook. Remote server response:", responseData);

    const twiml = new Twilio.twiml.MessagingResponse();
    return callback(null, twiml);

  } catch (error) {
    console.error("Failed to send request to remote endpoint:", error);
    return callback(error);
  }
};
