# Chester on Replit

## Run the web app

The project is a static browser application. Start it with:

```bash
npm start
```

The Replit workflow serves the app on port 5000 through `server.js`. The
TensorFlow.js models are served from the existing `models/` directory and are
loaded into the browser for local inference.

## Uploads

The browser accepts PNG/JPEG images and monochrome DICOM files, including
multi-frame studies and the common RLE, JPEG, JPEG-LS, and JPEG 2000 transfer
syntaxes. DICOM pixel data is decoded in the browser and is not uploaded to the
server. Multi-frame studies pause for frame selection before analysis.

This is an educational prototype and is **not for medical use**.