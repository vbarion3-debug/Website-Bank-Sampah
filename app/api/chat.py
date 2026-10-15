from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Header, Query
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc
from datetime import datetime
from app.core.timezone import now_wib
from app.core.database import get_db
from app.models.models import PesanChat, ChatAssignment, Nasabah
from app.schemas.chat import PesanChatResponse, ChatConversationResponse, ChatAssignmentResponse
from app.utils.security import get_current_nasabah, get_current_admin, get_session
from app.utils.chat_manager import manager

router = APIRouter(prefix="/chat", tags=["Livechat"])


# ========== WEBSOCKET: NASABAH ==========
@router.websocket("/ws/nasabah")
async def ws_chat_nasabah(websocket: WebSocket, token: str = Query(...), db: Session = Depends(get_db)):
    session = get_session(token)
    if not session or session.get("role") != "nasabah":
        await websocket.close(code=4401)
        return
    nik = session["sub"]

    await manager.connect_nasabah(nik, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            pesan = PesanChat(nik_nasabah=nik, sender_type="nasabah", isi_pesan=data["isi_pesan"])
            db.add(pesan)
            db.commit()
            db.refresh(pesan)

            payload = PesanChatResponse.model_validate(pesan).model_dump(mode="json")
            await manager.send_to_admins(nik, payload)
            # kirim balik konfirmasi ke pengirim juga, biar UI nasabah update instan
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        manager.disconnect_nasabah(nik)


# ========== WEBSOCKET: ADMIN (buka chat nasabah tertentu) ==========
@router.websocket("/ws/admin/{nik}")
async def ws_chat_admin(websocket: WebSocket, nik: str, token: str = Query(...), db: Session = Depends(get_db)):
    session = get_session(token)
    if not session or session.get("role") not in ("admin", "super_admin"):
        await websocket.close(code=4401)
        return
    admin_username = session["sub"]

    await manager.connect_admin(nik, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            pesan = PesanChat(
                nik_nasabah=nik,
                sender_type="admin",
                sender_id=admin_username,
                isi_pesan=data["isi_pesan"],
            )
            db.add(pesan)
            db.commit()
            db.refresh(pesan)

            payload = PesanChatResponse.model_validate(pesan).model_dump(mode="json")
            await manager.send_to_nasabah(nik, payload)
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        manager.disconnect_admin(nik, websocket)


# ========== REST: riwayat chat (load pertama kali / pagination) ==========
@router.get("/history/{nik}", response_model=list[PesanChatResponse])
def get_chat_history_admin(
    nik: str,
    authorization: str = Header(None),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Admin lihat riwayat chat nasabah tertentu berdasarkan NIK."""
    get_current_admin(authorization)

    history = (
        db.query(PesanChat)
        .filter(PesanChat.nik_nasabah == nik)
        .order_by(asc(PesanChat.created_at))
        .limit(limit)
        .all()
    )

    # Pesan dari nasabah yang belum dibaca, ditandai dibaca begitu admin buka chatnya
    db.query(PesanChat).filter(
        PesanChat.nik_nasabah == nik,
        PesanChat.sender_type == "nasabah",
        PesanChat.is_read == False,
    ).update({"is_read": True})
    db.commit()

    return history


@router.get("/history-nasabah", response_model=list[PesanChatResponse])
def get_chat_history_nasabah(
    authorization: str = Header(None),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Nasabah lihat riwayat chat-nya sendiri."""
    nik = get_current_nasabah(authorization)
    return (
        db.query(PesanChat)
        .filter(PesanChat.nik_nasabah == nik)
        .order_by(asc(PesanChat.created_at))
        .limit(limit)
        .all()
    )

# ========== REST: daftar percakapan (nasabah yang sudah chat) ==========
@router.get("/conversations", response_model=list[ChatConversationResponse])
def get_conversations(authorization: str = Header(None), db: Session = Depends(get_db)):
    """Admin lihat daftar nasabah yang sudah pernah mengirim chat."""
    get_current_admin(authorization)

    niks = [row[0] for row in db.query(PesanChat.nik_nasabah).distinct().all()]

    result = []
    for nik in niks:
        last_msg = (
            db.query(PesanChat)
            .filter(PesanChat.nik_nasabah == nik)
            .order_by(desc(PesanChat.created_at))
            .first()
        )
        unread_count = (
            db.query(PesanChat)
            .filter(
                PesanChat.nik_nasabah == nik,
                PesanChat.sender_type == "nasabah",
                PesanChat.is_read == False,
            )
            .count()
        )
        assignment = (
            db.query(ChatAssignment)
            .filter(ChatAssignment.nik_nasabah == nik)
            .first()
        )
        nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).first()

        result.append(
            ChatConversationResponse(
                nik_nasabah=nik,
                nama_nasabah=nasabah.nama_nasabah if nasabah else None,
                last_message=last_msg.isi_pesan if last_msg else None,
                last_message_at=last_msg.created_at if last_msg else None,
                last_sender_type=last_msg.sender_type if last_msg else None,
                unread_count=unread_count,
                handled_by=assignment.admin_username if assignment else None,
            )
        )

    result.sort(key=lambda c: c.last_message_at or datetime.min, reverse=True)
    return result


# ========== REST: ambil alih chat ==========
@router.post("/claim/{nik}", response_model=ChatAssignmentResponse)
def claim_chat(nik: str, authorization: str = Header(None), db: Session = Depends(get_db)):
    """Admin mengambil alih (atau merebut dari admin lain) chat nasabah tertentu."""
    admin_session = get_current_admin(authorization)
    admin_username = admin_session["sub"]

    assignment = db.query(ChatAssignment).filter(ChatAssignment.nik_nasabah == nik).first()
    if assignment:
        assignment.admin_username = admin_username
        assignment.claimed_at = now_wib()
    else:
        assignment = ChatAssignment(nik_nasabah=nik, admin_username=admin_username)
        db.add(assignment)

    db.commit()
    db.refresh(assignment)
    return assignment


# ========== REST: lepas chat ==========
@router.post("/release/{nik}")
def release_chat(nik: str, authorization: str = Header(None), db: Session = Depends(get_db)):
    """Admin melepas chat yang sedang ditanganinya."""
    get_current_admin(authorization)
    db.query(ChatAssignment).filter(ChatAssignment.nik_nasabah == nik).delete()
    db.commit()
    return {"message": "Chat dilepas."}