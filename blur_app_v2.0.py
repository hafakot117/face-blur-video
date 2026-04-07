#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_file
import os, uuid, base64, cv2
from werkzeug.utils import secure_filename
from face_tracker import FaceTracker

app = Flask(__name__)
os.makedirs('uploads', exist_ok=True)
os.makedirs('outputs', exist_ok=True)

sessions = {}

@app.route('/')
def index():
    return '''<!DOCTYPE html>
<html dir="rtl"><head><meta charset="UTF-8">
<title>🎭 Pro Face Blur</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;padding:20px}
.container{background:white;border-radius:20px;box-shadow:0 20px 60px rgba(0,0,0,0.3);padding:40px;max-width:1000px;margin:0 auto}
h1{text-align:center;color:#2d3748;margin-bottom:30px}
.step{display:none;animation:fadeIn 0.5s}
.step.active{display:block}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.upload-area{border:3px dashed #cbd5e0;border-radius:15px;padding:60px;text-align:center;cursor:pointer;margin-bottom:20px;transition:all 0.3s}
.upload-area:hover{border-color:#667eea;background:#f7fafc}
input[type=file]{display:none}
button{padding:15px 30px;background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;border-radius:10px;font-size:16px;font-weight:bold;cursor:pointer;margin:10px}
button:disabled{opacity:0.5}
.face-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:20px;margin:20px 0}
.face-card{border:3px solid #e2e8f0;border-radius:15px;padding:20px;text-align:center;cursor:pointer;transition:all 0.3s}
.face-card:hover{transform:translateY(-5px);box-shadow:0 10px 20px rgba(0,0,0,0.1)}
.face-card.selected{border-color:#48bb78;background:#f0fff4}
.face-card img{max-width:100%;border-radius:10px;margin-bottom:15px;box-shadow:0 4px 10px rgba(0,0,0,0.1)}
.face-card h3{margin:10px 0;color:#2d3748}
.face-card p{color:#718096;font-size:14px;margin-bottom:15px}
.face-card button{width:100%;margin:0}
.progress{height:30px;background:#e2e8f0;border-radius:15px;overflow:hidden;margin:20px 0;display:none}
.progress-fill{height:100%;background:linear-gradient(90deg,#667eea,#764ba2)}
.msg{padding:15px;border-radius:10px;margin:15px 0;text-align:center}
.success{background:#c8e6c9;color:#2e7d32}
.error{background:#ffcdd2;color:#c62828}
.info{background:#e3f2fd;color:#1976d2}
.blur-control{margin:20px 0;padding:20px;background:#f7fafc;border-radius:10px}
.blur-control label{display:block;margin-bottom:10px;font-weight:bold}
.blur-control input[type=range]{width:100%}
.blur-value{text-align:center;color:#667eea;font-size:24px;font-weight:bold;margin-top:10px}
</style>
</head><body>
<div class="container">
<h1>🎭 Pro Face Blur Studio</h1>

<div id="step1" class="step active">
<div class="upload-area" onclick="document.getElementById('file').click()">
<div style="font-size:60px">📹</div>
<h2>העלה וידאו</h2>
<p>גרור או לחץ</p>
<input type="file" id="file" accept="video/*">
</div>
<div id="msg1"></div>
</div>

<div id="step2" class="step">
<h2 style="text-align:center;margin-bottom:20px">בחר פרצופים לטשטש</h2>
<div id="faceGrid" class="face-grid"></div>
<div class="blur-control">
<label>עוצמת טשטוש</label>
<input type="range" id="blurSlider" min="21" max="151" step="10" value="101">
<div class="blur-value" id="blurValue">101</div>
</div>
<div style="text-align:center">
<button onclick="processVideo()">🎬 עבד וידאו</button>
</div>
</div>

<div id="step3" class="step">
<h2 style="text-align:center">מעבד...</h2>
<div class="progress" id="prog"><div class="progress-fill" style="width:100%"></div></div>
<div id="msg3" class="info">מנתח...</div>
</div>

<div id="step4" class="step">
<h2 style="text-align:center">✅ מוכן!</h2>
<button onclick="download()">⬇️ הורד</button>
<button onclick="location.reload()">🔄 חדש</button>
</div>

</div>
<script>
let sessionId, selectedFaces = new Set(), downloadUrl;

document.getElementById('file').onchange = e => e.target.files[0] && handleFile(e.target.files[0]);
document.getElementById('blurSlider').oninput = e => document.getElementById('blurValue').textContent = e.target.value;

async function handleFile(file){
  document.getElementById('msg1').innerHTML = '<div class="info">מעלה...</div>';
  const fd = new FormData();
  fd.append('video', file);
  try {
    const r = await fetch('/upload', {method:'POST', body:fd});
    const d = await r.json();
    if(d.success){
      sessionId = d.session_id;
      document.getElementById('msg1').innerHTML = '<div class="success">✅ הועלה!</div>';
      setTimeout(analyzeVideo, 1000);
    }
  } catch(e) {
    document.getElementById('msg1').innerHTML = '<div class="error">❌ שגיאה</div>';
  }
}

async function analyzeVideo(){
  document.getElementById('step1').classList.remove('active');
  document.getElementById('step3').classList.add('active');
  document.getElementById('prog').style.display = 'block';
  try {
    const r = await fetch('/analyze/' + sessionId);
    const d = await r.json();
    if(d.success) showFaceSelection(d);
  } catch(e) {
    document.getElementById('msg3').innerHTML = '<div class="error">❌ שגיאה</div>';
  }
}

function showFaceSelection(data){
  document.getElementById('step3').classList.remove('active');
  document.getElementById('step2').classList.add('active');
  const grid = document.getElementById('faceGrid');
  grid.innerHTML = '';
  data.faces.forEach(face => {
    const card = document.createElement('div');
    card.className = 'face-card';
    card.innerHTML = `
      <img src="data:image/jpeg;base64,${face.thumbnail}">
      <h3>פרצוף ${face.id}</h3>
      <p>${face.appearances} הופעות</p>
      <button onclick="toggleFace(${face.id}, this)">בחר לטשטוש</button>
    `;
    grid.appendChild(card);
  });
}

function toggleFace(id, btn){
  if(selectedFaces.has(id)){
    selectedFaces.delete(id);
    btn.parentElement.classList.remove('selected');
    btn.textContent = 'בחר לטשטוש';
  } else {
    selectedFaces.add(id);
    btn.parentElement.classList.add('selected');
    btn.textContent = '✓ נבחר';
  }
}

async function processVideo(){
  if(!selectedFaces.size) return alert('בחר פרצוף');
  document.getElementById('step2').classList.remove('active');
  document.getElementById('step3').classList.add('active');
  document.getElementById('msg3').innerHTML = '<div class="info">מעבד...</div>';
  const fd = new FormData();
  fd.append('faces', JSON.stringify([...selectedFaces]));
  fd.append('blur', document.getElementById('blurSlider').value);
  try {
    const r = await fetch('/process/' + sessionId, {method:'POST', body:fd});
    const d = await r.json();
    if(d.success){
      downloadUrl = d.download_url;
      document.getElementById('step3').classList.remove('active');
      document.getElementById('step4').classList.add('active');
    }
  } catch(e) {
    document.getElementById('msg3').innerHTML = '<div class="error">❌ שגיאה</div>';
  }
}

function download(){
  window.location.href = downloadUrl;
}
</script>
</body></html>'''

@app.route('/upload', methods=['POST'])
def upload():
    try:
        f = request.files['video']
        sid = str(uuid.uuid4())
        ext = f.filename.rsplit('.', 1)[1]
        inp = f'uploads/{sid}.{ext}'
        f.save(inp)
        sessions[sid] = {'input': inp}
        return jsonify({'success': True, 'session_id': sid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_face_thumbnails(tracker, unique_faces):
    thumbnails = {}
    for uid, data in unique_faces.items():
        tracker.cap.set(cv2.CAP_PROP_POS_FRAMES, data['best_frame'])
        ret, frame = tracker.cap.read()
        if ret:
            x, y, w, h = data['best_bbox']
            padding = int(h * 0.3)
            y1, y2 = max(0, y - padding), min(frame.shape[0], y + h + padding)
            x1, x2 = max(0, x - padding), min(frame.shape[1], x + w + padding)
            face_crop = frame[y1:y2, x1:x2]
            h_crop, w_crop = face_crop.shape[:2]
            if h_crop > 200 or w_crop > 200:
                scale = 200 / max(h_crop, w_crop)
                face_crop = cv2.resize(face_crop, (int(w_crop * scale), int(h_crop * scale)))
            _, buffer = cv2.imencode('.jpg', face_crop)
            thumbnails[uid] = base64.b64encode(buffer).decode()
    tracker.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return thumbnails

@app.route('/analyze/<sid>')
def analyze(sid):
    try:
        sess = sessions[sid]
        tracker = FaceTracker(sess['input'])
        sampled = tracker.sample_video_frames(sample_interval=15)
        unique_faces = tracker.cluster_faces(sampled)
        thumbnails = get_face_thumbnails(tracker, unique_faces)
        sess['tracker'] = tracker
        tracker.unique_faces = unique_faces
        faces_info = [{'id': uid, 'appearances': len(data['frames']), 'thumbnail': thumbnails[uid]} 
                     for uid, data in unique_faces.items()]
        return jsonify({'success': True, 'faces': faces_info})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/process/<sid>', methods=['POST'])
def process(sid):
    try:
        import json
        faces_to_blur = json.loads(request.form['faces'])
        blur_strength = int(request.form.get('blur', 101))
        sess = sessions[sid]
        out = f'outputs/{sid}_blurred.mp4'
        sess['tracker'].process_video(out, faces_to_blur, blur_strength=blur_strength)
        return jsonify({'success': True, 'download_url': f'/download/{sid}_blurred.mp4'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/<fn>')
def download(fn):
    return send_file(f'outputs/{secure_filename(fn)}', as_attachment=True)

if __name__ == '__main__':
    print("\n🎭 Pro Face Blur Studio")
    print("📱 http://localhost:5000\n")
    app.run(host='0.0.0.0', port=5000)
