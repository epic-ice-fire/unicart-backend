"""
UniCart Email Notification Service
Uses Gmail SMTP (free). Set GMAIL_USER and GMAIL_APP_PASSWORD in your .env file.

To get a Gmail App Password:
  1. Enable 2-Step Verification at myaccount.google.com → Security → 2-Step Verification
  2. Once 2FA is ON, go to myaccount.google.com/apppasswords
  3. Create one → name it "UniCart" → copy the 16-character password (no spaces)
  4. Add to .env:
       GMAIL_USER=yourgmail@gmail.com
       GMAIL_APP_PASSWORD=abcdabcdabcdabcd   ← 16 chars, no spaces
       ADMIN_EMAIL=unicartbytekena@gmail.com
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("unicart.email")
FROM_NAME = "UniCart"


def _get_credentials():
    """Import lazily so settings are fully loaded before we read them."""
    from app.config import settings
    return settings.GMAIL_USER, settings.GMAIL_APP_PASSWORD


def _send(to_email: str, subject: str, html_body: str) -> bool:
    """Send a single email. Returns True on success, False on failure."""
    gmail_user, gmail_password = _get_credentials()

    if not gmail_user or not gmail_password:
        logger.warning(
            f"[UniCart Email] NOT CONFIGURED — skipping email to {to_email}. "
            "Add GMAIL_USER and GMAIL_APP_PASSWORD to your .env file."
        )
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{FROM_NAME} <{gmail_user}>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_email, msg.as_string())

        logger.info(f"[UniCart Email] ✅ Sent → {to_email} | {subject}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            f"[UniCart Email] ❌ Authentication failed for {gmail_user}. "
            "Make sure you are using a Gmail App Password (not your normal Gmail password). "
            "Go to myaccount.google.com/apppasswords to generate one."
        )
        return False
    except Exception as e:
        logger.error(f"[UniCart Email] ❌ Failed to send to {to_email}: {e}")
        return False


def _base_template(content: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <style>
        body {{ font-family: Arial, sans-serif; background: #f6f8f7; margin: 0; padding: 24px; color: #101828; }}
        .card {{ max-width: 560px; margin: 0 auto; background: white; border-radius: 20px;
                 border: 1px solid #E4E7EC; padding: 32px; }}
        .header {{ background: linear-gradient(135deg, #1F7A4C, #2E8B57); border-radius: 14px;
                   padding: 24px; margin-bottom: 24px; }}
        .header h1 {{ color: white; margin: 0; font-size: 22px; }}
        .header p {{ color: #DFF5E7; margin: 8px 0 0; font-size: 14px; }}
        .header.danger {{ background: linear-gradient(135deg, #B42318, #DC2626); }}
        .pill {{ display: inline-block; padding: 6px 14px; border-radius: 999px; font-size: 13px;
                 font-weight: 700; border: 1px solid #E4E7EC; background: #F9FAFB;
                 color: #344054; margin: 4px 4px 4px 0; }}
        .pill.green {{ background: #ECFDF3; border-color: #4BB543; color: #027A48; }}
        .pill.blue {{ background: #EFF8FF; border-color: #53B1FD; color: #175CD3; }}
        .pill.yellow {{ background: #FFF7E6; border-color: #FAC515; color: #7A2E0E; }}
        .pill.red {{ background: #FEF3F2; border-color: #FECACA; color: #B42318; }}
        .pill.purple {{ background: #F4F3FF; border-color: #9E77ED; color: #5925DC; }}
        .item-box {{ background: #F9FAFB; border: 1px solid #E4E7EC; border-radius: 12px;
                     padding: 14px; margin: 8px 0; word-break: break-all; font-size: 13px; }}
        .alert-box {{ background: #FEF3F2; border: 1px solid #FECACA; border-radius: 12px;
                      padding: 14px; margin: 12px 0; }}
        .code-box {{ background: #F2F4F7; border: 1px solid #D0D5DD; border-radius: 12px;
                     padding: 20px; margin: 12px 0; text-align: center; }}
        .code-box span {{ font-size: 32px; font-weight: 900; letter-spacing: 8px; color: #101828; }}
        .footer {{ margin-top: 24px; font-size: 12px; color: #667085; text-align: center; }}
        h2 {{ color: #101828; font-size: 18px; margin: 0 0 12px; }}
        p {{ color: #475467; line-height: 1.6; margin: 0 0 12px; }}
        .divider {{ border: none; border-top: 1px solid #E4E7EC; margin: 20px 0; }}
      </style>
    </head>
    <body>
      <div class="card">
        {content}
        <div class="footer">
          UniCart — Campus Group Buying Platform<br>
          Pan-Atlantic University · This is an automated notification.
        </div>
      </div>
    </body>
    </html>
    """


# ─── PAU Verification Email ─────────────────────────────────────────────────────

def send_pau_verification_code(
    pau_email: str,
    code: str,
    expires_minutes: int,
) -> None:
    subject = "Your UniCart student verification code"
    content = f"""
    <div class="header">
      <h1>Verify your PAU email</h1>
      <p>Enter this code in the UniCart app to complete verification.</p>
    </div>

    <h2>Your verification code</h2>
    <p>Use the code below to verify your Pan-Atlantic University email address.
    This code expires in <strong>{expires_minutes} minutes</strong>.</p>

    <div class="code-box">
      <span>{code}</span>
    </div>

    <p style="color: #667085; font-size: 13px;">
      If you did not request this code, please ignore this email.
      Do not share this code with anyone.
    </p>
    """
    _send(pau_email, subject, _base_template(content))


# ─── Admin: Item force-removed notification ────────────────────────────────────

def send_admin_item_force_removed(
    admin_email: str,
    item_id: int,
    lobby_id: int,
    item_link: str,
    item_amount: int,
    was_paid: bool,
    user_email: str,
    removed_by: str,
) -> None:
    subject = f"⚠️ UniCart Admin — Item #{item_id} force-removed from Lobby #{lobby_id}"
    paid_note = (
        '<span class="pill red">⚠️ Item was PAID — no refund issued</span>'
        if was_paid else
        '<span class="pill">Item was unpaid</span>'
    )
    content = f"""
    <div class="header danger">
      <h1>Item #{item_id} Force-Removed</h1>
      <p>Admin action log — Lobby #{lobby_id}</p>
    </div>

    <h2>Removal Summary</h2>
    <div>
      <span class="pill">Item ID: #{item_id}</span>
      <span class="pill blue">Lobby: #{lobby_id}</span>
      <span class="pill yellow">Amount: ₦{item_amount:,}</span>
      {paid_note}
    </div>

    <hr class="divider">

    <h2>Item Link</h2>
    <div class="item-box"><a href="{item_link}">{item_link}</a></div>

    <hr class="divider">

    <p><strong>Submitted by:</strong> {user_email}</p>
    <p><strong>Removed by admin:</strong> {removed_by}</p>
    <p style="color: #B42318; font-weight: 700;">
      ⚠️ No refund has been issued per UniCart's no-refund policy for fraudulent submissions.
    </p>
    """
    _send(admin_email, subject, _base_template(content))


# ─── User: Item force-removed notification ─────────────────────────────────────

def send_user_item_force_removed(
    user_email: str,
    item_id: int,
    lobby_id: int,
    item_link: str,
    item_amount: int,
    was_paid: bool,
) -> None:
    subject = f"UniCart — Important notice regarding your item in Batch #{lobby_id}"
    refund_note = ""
    if was_paid:
        refund_note = """
        <div class="alert-box">
          <p style="color: #B42318; font-weight: 700; margin: 0;">
            ⚠️ No-Refund Notice<br><br>
            <span style="font-weight: 400; color: #475467;">
            Your item was paid for. However, as per UniCart's Terms of Service,
            <strong>no refund will be issued</strong> for items removed due to fraudulent
            submissions, fabricated amounts, or violations of our platform guidelines.
            If you believe this removal was made in error, please contact
            <a href="mailto:unicartbytekena@gmail.com">unicartbytekena@gmail.com</a>
            with your reference details.
            </span>
          </p>
        </div>
        """

    content = f"""
    <div class="header danger">
      <h1>Your item has been removed</h1>
      <p>Action taken by the UniCart admin team — Lobby #{lobby_id}</p>
    </div>

    <p>
      Dear UniCart member,<br><br>
      We are writing to inform you that one of your submitted items in
      <strong>Lobby #{lobby_id}</strong> has been reviewed and subsequently
      <strong>removed by the UniCart administration team</strong>.
      This action was taken in accordance with UniCart's platform guidelines,
      which prohibit fraudulent submissions, fabricated amounts, and links
      that violate our Terms of Service.
    </p>

    <h2>Removed Item</h2>
    <div class="item-box">
      <strong>Item ID:</strong> #{item_id}<br>
      <strong>Item Amount:</strong> ₦{item_amount:,}<br>
      <strong>Link:</strong> <a href="{item_link}">{item_link}</a>
    </div>

    {refund_note}

    <hr class="divider">

    <h2>What this means for you</h2>
    <p>
      Your remaining active items in the lobby are unaffected. You may continue
      participating in the current batch with your other paid items.
      If this was your only item, your lobby pass remains valid and you may
      add a new compliant item.
    </p>

    <p>
      If you have questions or concerns, please contact the UniCart admin team at
      <a href="mailto:unicartbytekena@gmail.com">unicartbytekena@gmail.com</a>.
    </p>

    <p style="color: #027A48; font-weight: 700;">
      Thank you for your understanding and continued participation in UniCart.
    </p>
    """
    _send(user_email, subject, _base_template(content))


# ─── Lobby triggered: Admin email ──────────────────────────────────────────────

def send_admin_lobby_triggered(
    admin_email: str,
    lobby_id: int,
    target_amount: int,
    final_amount: int,
    member_count: int,
    total_revenue_ngn: int,
    unique_paying_members: int,
) -> None:
    subject = f"🎯 UniCart — Lobby #{lobby_id} has reached its target"
    content = f"""
    <div class="header">
      <h1>Lobby #{lobby_id} triggered</h1>
      <p>The vault has reached its target. Action required.</p>
    </div>

    <h2>Batch Summary</h2>
    <p>Lobby <strong>#{lobby_id}</strong> has been triggered and is now awaiting your processing.</p>

    <div>
      <span class="pill green">Target: ₦{target_amount:,}</span>
      <span class="pill green">Final paid total: ₦{final_amount:,}</span>
      <span class="pill blue">Members in lobby: {member_count}</span>
      <span class="pill blue">Unique paying members: {unique_paying_members}</span>
      <span class="pill yellow">Total entry fee revenue: ₦{total_revenue_ngn:,}</span>
    </div>

    <hr class="divider">

    <h2>Next Steps</h2>
    <p>
      1. Log in to the UniCart admin dashboard.<br>
      2. Review all paid items for this batch.<br>
      3. Click <strong>"Start Processing"</strong> to begin order placement.<br>
      4. Update the batch status as you progress through delivery.
    </p>

    <p style="color: #B42318; font-weight: 700;">
      ⚠️ Note: Any unpaid items have been automatically removed from this batch.
      Only paid and locked items are included in the order.
    </p>
    """
    _send(admin_email, subject, _base_template(content))


# ─── Lobby triggered: User email ───────────────────────────────────────────────

def send_user_lobby_triggered(
    user_email: str,
    lobby_id: int,
    target_amount: int,
    final_amount: int,
    my_paid_item_count: int,
    my_paid_total: int,
    item_links: list[str],
) -> None:
    subject = f"🛒 UniCart — Your batch #{lobby_id} has been triggered!"

    items_html = ""
    if item_links:
        items_html = "<h2>Your locked items</h2>"
        for i, link in enumerate(item_links, 1):
            items_html += f'<div class="item-box">#{i} — <a href="{link}">{link}</a></div>'
    else:
        items_html = "<p>You had no paid items in this batch.</p>"

    content = f"""
    <div class="header">
      <h1>Your batch is locked in! 🎉</h1>
      <p>Lobby #{lobby_id} has reached its target and is being processed.</p>
    </div>

    <p>
      Great news! The group buying vault for Lobby <strong>#{lobby_id}</strong> has reached
      its target of <strong>₦{target_amount:,}</strong>. Your paid items are locked in and
      will be included in this batch order.
    </p>

    <div>
      <span class="pill green">Vault total: ₦{final_amount:,}</span>
      <span class="pill green">Your paid items: {my_paid_item_count}</span>
      <span class="pill blue">Your paid total: ₦{my_paid_total:,}</span>
    </div>

    <hr class="divider">

    {items_html}

    <hr class="divider">

    <h2>What happens next?</h2>
    <p>
      The admin will begin processing your order shortly. You will receive updates
      as the batch moves through each stage — processing, in transit, and delivery.
      Keep an eye on your inbox!
    </p>

    <p style="color: #027A48; font-weight: 700;">
      Thank you for participating in UniCart campus group buying.
    </p>
    """
    _send(user_email, subject, _base_template(content))


# ─── Status change: User email ─────────────────────────────────────────────────

STATUS_TITLES = {
    "triggered": ("🎯 Your batch has been triggered!", "Target reached"),
    "processing": ("📦 Your order is being processed", "Processing"),
    "in_transit": ("🚚 Your order is on the way!", "In Transit"),
    "completed": ("✅ Your order has been delivered!", "Delivered"),
}

STATUS_DESCRIPTIONS = {
    "triggered": (
        "The vault has reached its target. Your paid items are locked in and the "
        "admin is reviewing the batch to begin placing your order."
    ),
    "processing": (
        "The admin is currently preparing and placing your group order. "
        "Your items are being confirmed with the supplier."
    ),
    "in_transit": (
        "Your shared order has been placed and is now on its way! "
        "The admin will update you once it has been delivered."
    ),
    "completed": (
        "Your order has been delivered! We hope you enjoy your items. "
        "Thank you for using UniCart campus group buying."
    ),
}

STATUS_COLORS = {
    "triggered": "yellow",
    "processing": "blue",
    "in_transit": "purple",
    "completed": "green",
}


def send_user_batch_status_update(
    user_email: str,
    lobby_id: int,
    new_status: str,
    my_paid_item_count: int,
    my_paid_total: int,
    item_links: list[str],
) -> None:
    title, badge = STATUS_TITLES.get(
        new_status, (f"Batch #{lobby_id} update", new_status.replace("_", " ").title())
    )
    description = STATUS_DESCRIPTIONS.get(new_status, "Your batch status has been updated.")
    color = STATUS_COLORS.get(new_status, "")

    subject = f"UniCart — {title} (Batch #{lobby_id})"

    items_html = ""
    if item_links:
        items_html = "<h2>Your items in this batch</h2>"
        for i, link in enumerate(item_links, 1):
            items_html += f'<div class="item-box">#{i} — <a href="{link}">{link}</a></div>'

    content = f"""
    <div class="header">
      <h1>{title}</h1>
      <p>Batch #{lobby_id} — status update</p>
    </div>

    <div style="margin-bottom: 16px;">
      <span class="pill {color}">{badge}</span>
    </div>

    <p>{description}</p>

    <div>
      <span class="pill">Your paid items: {my_paid_item_count}</span>
      <span class="pill green">Your paid total: ₦{my_paid_total:,}</span>
    </div>

    <hr class="divider">

    {items_html if item_links else ""}
    """
    _send(user_email, subject, _base_template(content))