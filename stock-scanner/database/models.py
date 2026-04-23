from sqlalchemy import (create_engine, Column, Integer, String, Float,
                         DateTime, Boolean, Text, Enum, ForeignKey)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Weinstein 스캔 결과 ──────────────────────────────────────────

class ScanResult(Base):
    __tablename__ = "scan_results"

    id = Column(Integer, primary_key=True, index=True)
    scan_time = Column(DateTime, default=datetime.utcnow, index=True)
    market = Column(String(10), index=True)   # KR / US
    ticker = Column(String(20), index=True)
    name = Column(String(100))
    signal_type = Column(String(20))          # BREAKOUT / RE_BREAKOUT / REBOUND
    stage = Column(String(10))
    price = Column(Float)
    ma150 = Column(Float)
    volume = Column(Float)
    volume_avg = Column(Float)
    volume_ratio = Column(Float)
    signal_date = Column(String(10))          # YYYY-MM-DD
    notified = Column(Boolean, default=False)
    # ── 확장 메타데이터 (nullable) ──────────────────────────────
    pivot_price      = Column(Float,       nullable=True)   # 돌파 기준 pivot 가격
    support_level    = Column(Float,       nullable=True)   # MA50 지지선
    market_condition = Column(String(20),  nullable=True)   # BULL/BEAR/CAUTION/NEUTRAL
    signal_quality   = Column(String(10),  nullable=True)   # STRONG/MODERATE/WEAK
    rs_value         = Column(Float,       nullable=True)   # legacy ratio RS 값
    grade            = Column(String(5),   nullable=True)   # S/A/B 종합 등급
    # ── Weinstein v2 주봉/RS 메타데이터 (nullable) ─────────────
    weekly_stage        = Column(String(10), nullable=True)  # 주봉 30-SMA 기준 Stage
    sma30w              = Column(Float,      nullable=True)  # 30주 SMA
    sma10w              = Column(Float,      nullable=True)  # 10주 SMA
    weekly_volume_ratio = Column(Float,      nullable=True)  # 주봉 거래량 배율
    mansfield_rs        = Column(Float,      nullable=True)  # Mansfield RS
    rs_trend            = Column(String(10), nullable=True)  # RISING/FALLING/FLAT
    base_weeks          = Column(Float,      nullable=True)  # 주봉 base 기간
    base_width_pct      = Column(Float,      nullable=True)  # 주봉 base 폭
    warning_flags       = Column(Text,       nullable=True)  # JSON list[str]


class ScanLog(Base):
    __tablename__ = "scan_logs"

    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    market = Column(String(10))
    total_scanned = Column(Integer, default=0)
    signals_found = Column(Integer, default=0)
    status = Column(String(20), default="RUNNING")  # RUNNING / DONE / ERROR
    error_msg = Column(Text, default="")
    triggered_by = Column(String(20), default="manual")


# ── 계좌 및 거래 관리 ────────────────────────────────────────────

class Account(Base):
    """계좌 (여러 계좌 지원)"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    # KR_STOCK / US_STOCK / KR_PENSION / KR_IRP / KR_ISA / OTHER
    account_type = Column(String(20), default="KR_STOCK", server_default="KR_STOCK")
    currency = Column(String(10), default="KRW")  # KRW / USD (account_type으로 자동 결정)
    broker = Column(String(50), default="")        # 증권사 (키움, 한국투자, ...)
    memo = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    transactions = relationship("Transaction", back_populates="account",
                                order_by="Transaction.trade_date.desc()")
    holdings = relationship("Holding", back_populates="account")

    @property
    def cash_balance(self):
        """입출금 + 매수/매도 후 현금 잔고 계산"""
        bal = 0.0
        for t in self.transactions:
            if t.tx_type == "DEPOSIT":
                bal += t.amount
            elif t.tx_type == "WITHDRAW":
                bal -= t.amount
            elif t.tx_type == "BUY":
                bal -= t.amount + (t.fee or 0)
            elif t.tx_type == "SELL":
                bal += t.amount - (t.fee or 0) - (t.tax or 0)
        return round(bal, 2)


class Transaction(Base):
    """거래 일지 (매수/매도/입금/출금)"""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    tx_type = Column(String(10), nullable=False)  # BUY / SELL / DEPOSIT / WITHDRAW
    trade_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    ticker = Column(String(20), nullable=True)
    name = Column(String(100), nullable=True)
    market = Column(String(10), nullable=True)    # KR / US
    quantity = Column(Float, nullable=True)       # 수량
    price = Column(Float, nullable=True)          # 단가
    amount = Column(Float, nullable=False)        # 총금액 (price * quantity 또는 입출금액)
    fee = Column(Float, default=0)               # 수수료
    tax = Column(Float, default=0)               # 세금 (매도세)
    memo = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account", back_populates="transactions")


class Holding(Base):
    """현재 보유 주식 (평단가 자동 계산)"""
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    ticker = Column(String(20), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    market = Column(String(10), nullable=False)   # KR / US
    quantity = Column(Float, default=0)           # 보유 수량
    avg_price = Column(Float, default=0)          # 평단가
    current_price = Column(Float, nullable=True)  # 현재가 (캐시)
    price_updated_at = Column(DateTime, nullable=True)
    memo = Column(Text, default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account", back_populates="holdings")

    @property
    def eval_amount(self):
        if self.current_price and self.quantity:
            return round(self.current_price * self.quantity, 2)
        return round(self.avg_price * self.quantity, 2)

    @property
    def profit_loss(self):
        if not self.current_price:
            return 0.0
        return round((self.current_price - self.avg_price) * self.quantity, 2)

    @property
    def profit_loss_pct(self):
        if not self.current_price or not self.avg_price:
            return 0.0
        return round((self.current_price - self.avg_price) / self.avg_price * 100, 2)


# ── Weinstein 감시 목록 (매도 시그널 알림용) ─────────────────────

class WatchList(Base):
    """Weinstein 매도 시그널 감시 종목"""
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(20), unique=True, index=True)
    name = Column(String(100))
    market = Column(String(10))
    buy_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    memo = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate()
    db = SessionLocal()
    try:
        if db.query(Account).count() == 0:
            db.add(Account(name="국내 주식 계좌", account_type="KR_STOCK", currency="KRW"))
            db.add(Account(name="해외 주식 계좌", account_type="US_STOCK", currency="USD"))
            db.commit()
    finally:
        db.close()


def _migrate():
    """기존 DB에 새 컬럼이 없으면 추가 (ALTER TABLE)"""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        def _existing_cols(table):
            return {row[1] for row in conn.execute(_text(f"PRAGMA table_info({table})"))}

        # accounts 테이블
        acct_cols = _existing_cols("accounts")
        for col, ddl in [
            ("account_type", "ALTER TABLE accounts ADD COLUMN account_type VARCHAR(20) DEFAULT 'KR_STOCK'"),
            ("broker",       "ALTER TABLE accounts ADD COLUMN broker VARCHAR(50) DEFAULT ''"),
        ]:
            if col not in acct_cols:
                try:
                    conn.execute(_text(ddl)); conn.commit()
                except Exception:
                    pass

        # scan_results 테이블 — 새 메타데이터 컬럼
        sr_cols = _existing_cols("scan_results")
        for col, ddl in [
            ("pivot_price",      "ALTER TABLE scan_results ADD COLUMN pivot_price REAL"),
            ("support_level",    "ALTER TABLE scan_results ADD COLUMN support_level REAL"),
            ("market_condition", "ALTER TABLE scan_results ADD COLUMN market_condition VARCHAR(20)"),
            ("signal_quality",   "ALTER TABLE scan_results ADD COLUMN signal_quality VARCHAR(10)"),
            ("rs_value",         "ALTER TABLE scan_results ADD COLUMN rs_value REAL"),
            ("grade",            "ALTER TABLE scan_results ADD COLUMN grade VARCHAR(5)"),
            ("weekly_stage",        "ALTER TABLE scan_results ADD COLUMN weekly_stage VARCHAR(10)"),
            ("sma30w",              "ALTER TABLE scan_results ADD COLUMN sma30w REAL"),
            ("sma10w",              "ALTER TABLE scan_results ADD COLUMN sma10w REAL"),
            ("weekly_volume_ratio", "ALTER TABLE scan_results ADD COLUMN weekly_volume_ratio REAL"),
            ("mansfield_rs",        "ALTER TABLE scan_results ADD COLUMN mansfield_rs REAL"),
            ("rs_trend",            "ALTER TABLE scan_results ADD COLUMN rs_trend VARCHAR(10)"),
            ("base_weeks",          "ALTER TABLE scan_results ADD COLUMN base_weeks REAL"),
            ("base_width_pct",      "ALTER TABLE scan_results ADD COLUMN base_width_pct REAL"),
            ("warning_flags",       "ALTER TABLE scan_results ADD COLUMN warning_flags TEXT"),
        ]:
            if col not in sr_cols:
                try:
                    conn.execute(_text(ddl)); conn.commit()
                except Exception:
                    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
