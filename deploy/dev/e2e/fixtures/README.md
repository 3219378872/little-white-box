These tiny synthetic fixtures were generated for the media upload regressions:
`clip.mp4` is 0.3 seconds of blue H.264 video (64x64); `voice.wav` is 0.25 seconds
of silent PCM (mono, 8 kHz, 16 bit); M4A/AAC and MP3 versions encode that silence.
No user media, speech, or third-party content is included. Tests append an ISO
`free` box when exercising large-video transport, retaining the valid clip.
