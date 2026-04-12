import "dart:async";
import "package:flutter/material.dart";
import "package:url_launcher/url_launcher.dart";
import "../../models/lobby.dart";
import "../../services/auth_service.dart";
import "../../services/lobby_service.dart";
import "../../services/session_service.dart";
import "../admin/admin_dashboard_screen.dart";
import "../auth/login_screen.dart";
import "../auth/verify_pau_screen.dart";

class LobbyScreen extends StatefulWidget {
  final String token;
  const LobbyScreen({super.key, required this.token});

  @override
  State<LobbyScreen> createState() => _LobbyScreenState();
}

class _LobbyScreenState extends State<LobbyScreen> {
  Lobby? lobby;
  bool isLoading = true;
  bool isBusy = false;
  String? error;

  Map<String, dynamic>? meData;
  Map<String, dynamic>? myItemsData;
  Map<String, dynamic>? myHistoryData;
  Map<String, dynamic>? mainDetailsData;

  String? pendingPaymentReference;
  String? pendingPaymentUrl;

  Timer? _pollTimer;

  final TextEditingController itemLinkController = TextEditingController();
  final TextEditingController itemAmountController = TextEditingController();

  @override
  void initState() {
    super.initState();
    loadAll();
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    itemLinkController.dispose();
    itemAmountController.dispose();
    super.dispose();
  }

  void _startPolling() {
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (mounted) loadAll(silent: true);
    });
  }

  void _stopPolling() {
    _pollTimer?.cancel();
    _pollTimer = null;
  }

  Future<void> loadAll({bool silent = false}) async {
    if (!silent) setState(() { isLoading = true; error = null; });
    try {
      Map<String, dynamic>? me, myItems, myHistory, details;
      try { me = await AuthService.me(widget.token); } catch (_) {}
      try { details = await LobbyService.mainLobbyDetails(widget.token); } catch (_) {}
      try { myItems = await LobbyService.myMainLobbyItems(widget.token); } catch (_) {}
      try { myHistory = await LobbyService.myBatchHistory(widget.token); } catch (_) {}
      if (!mounted) return;
      setState(() {
        meData = me;
        myItemsData = myItems;
        myHistoryData = myHistory;
        mainDetailsData = details;
        if (details != null) {
          lobby = Lobby(
            lobbyId: details["lobby_id"] as int? ?? 0,
            status: details["status"]?.toString() ?? "open",
            currentItemAmount: (details["current_item_amount"] as num?)?.toInt() ?? 0,
            targetItemAmount: (details["target_item_amount"] as num?)?.toInt() ?? 0,
            memberCount: details["member_count"] as int? ?? 0,
          );
        }
        final backendRef = details?["pending_payment_reference"]?.toString();
        if (backendRef != null && backendRef.isNotEmpty) {
          pendingPaymentReference = backendRef;
        } else if (details?["has_pending_payment"] != true) {
          pendingPaymentReference = null;
          pendingPaymentUrl = null;
        }
      });
      final hasPending = details?["has_pending_payment"] == true || pendingPaymentReference != null;
      final hasItemPending = (myItems?["items"] as List? ?? []).any(
        (i) => i is Map && i["item_payment_status"] == "pending");
      if (hasPending || hasItemPending) { _startPolling(); } else { _stopPolling(); }
    } catch (e) {
      if (!silent) setState(() => error = e.toString());
    } finally {
      if (mounted && !silent) setState(() => isLoading = false);
    }
  }

  Future<void> logout() async {
    _stopPolling();
    await SessionService.clearToken();
    if (!mounted) return;
    Navigator.pushAndRemoveUntil(context,
        MaterialPageRoute(builder: (_) => const LoginScreen()), (r) => false);
  }

  Future<void> openVerifyScreen() async {
    final result = await Navigator.push(context,
        MaterialPageRoute(builder: (_) => VerifyPauScreen(token: widget.token)));
    if (result == true) await loadAll();
  }

  Future<void> openAdminDashboard() async {
    await Navigator.push(context,
        MaterialPageRoute(builder: (_) => AdminDashboardScreen(token: widget.token)));
    await loadAll();
  }

  Future<void> startEntryFeePayment() async {
    setState(() => isBusy = true);
    try {
      final response = await LobbyService.initializeEntryFeePayment(widget.token);
      final reference = response["reference"]?.toString();
      final authUrl = response["authorization_url"]?.toString();
      setState(() { pendingPaymentReference = reference; pendingPaymentUrl = authUrl; });
      if (authUrl != null && authUrl.isNotEmpty) {
        final launched = await launchUrl(Uri.parse(authUrl),
            mode: LaunchMode.platformDefault, webOnlyWindowName: "_blank");
        _startPolling();
        showMessage(launched
            ? "Paystack opened. Pay then tap \"I've Paid\" when you return."
            : "Could not open Paystack. Tap \"Open Paystack\" below to try again.");
      }
    } catch (e) { showMessage(e.toString()); }
    finally { if (mounted) setState(() => isBusy = false); }
  }

  Future<void> verifyEntryFeePayment() async {
    final reference = pendingPaymentReference;
    if (reference == null || reference.isEmpty) {
      showMessage("No pending payment found. Try refreshing."); return;
    }
    setState(() => isBusy = true);
    try {
      final response = await LobbyService.verifyEntryFeePayment(widget.token, reference: reference);
      if (response["status"]?.toString() == "success") {
        setState(() { pendingPaymentReference = null; pendingPaymentUrl = null; });
        _stopPolling();
        showMessage("Payment confirmed! You've joined the lobby.", isSuccess: true);
      } else {
        showMessage(response["message"]?.toString() ?? "Payment not confirmed yet. Try again in a moment.");
      }
      await loadAll();
    } catch (e) { showMessage(e.toString()); }
    finally { if (mounted) setState(() => isBusy = false); }
  }

  Future<void> reopenPaymentLink() async {
    if (pendingPaymentUrl == null || pendingPaymentUrl!.isEmpty) {
      showMessage("No payment link. Tap Pay to get a new one."); return;
    }
    await launchUrl(Uri.parse(pendingPaymentUrl!),
        mode: LaunchMode.platformDefault, webOnlyWindowName: "_blank");
  }

  Future<void> leaveLobby() async {
    final confirmed = await showDialog<bool>(context: context,
        builder: (ctx) => AlertDialog(
          title: const Text("Leave lobby?"),
          content: const Text("You will need to pay the entry fee again to rejoin. Unpaid items will be removed."),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text("Cancel")),
            ElevatedButton(onPressed: () => Navigator.pop(ctx, true), child: const Text("Leave")),
          ],
        ));
    if (confirmed != true) return;
    setState(() => isBusy = true);
    try {
      final r = await LobbyService.leaveLobby(widget.token);
      await loadAll();
      showMessage(r["message"]?.toString() ?? "Left lobby");
    } catch (e) { showMessage(e.toString()); }
    finally { if (mounted) setState(() => isBusy = false); }
  }

  Future<void> addItem() async {
    final amountRaw = double.tryParse(itemAmountController.text.trim());
    final amount = amountRaw != null ? double.parse(amountRaw.toStringAsFixed(2)) : null;
    final itemLink = itemLinkController.text.trim();
    if (itemLink.isEmpty || amount == null || amount <= 0) {
      showMessage("Enter a valid item link and amount."); return;
    }
    setState(() => isBusy = true);
    try {
      final response = await LobbyService.addItem(widget.token, itemLink: itemLink, itemAmount: amount.toInt());
      itemLinkController.clear(); itemAmountController.clear();
      await loadAll();
      showMessage(response["message"]?.toString() ?? "Item added. Pay for it to lock it in.", isSuccess: true);
    } catch (e) { showMessage(e.toString()); }
    finally { if (mounted) setState(() => isBusy = false); }
  }

  Future<void> removeItem(int itemId) async {
    setState(() => isBusy = true);
    try {
      final r = await LobbyService.removeItem(widget.token, itemId: itemId);
      await loadAll();
      showMessage(r["message"]?.toString() ?? "Item removed.");
    } catch (e) { showMessage(e.toString()); }
    finally { if (mounted) setState(() => isBusy = false); }
  }

  Future<void> payForItem(int itemId) async {
    // Guest price confirmation dialog
    final confirmed = await showDialog<bool>(context: context,
        builder: (ctx) => AlertDialog(
          title: const Row(children: [
            Icon(Icons.warning_amber_rounded, color: Color(0xFFB54708)),
            SizedBox(width: 8),
            Flexible(child: Text("Confirm guest price", style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16))),
          ]),
          content: const SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text("Before paying, confirm your price is the guest (logged-out) price.", style: TextStyle(fontSize: 14, height: 1.5)),
              SizedBox(height: 14),
              Text("✅  I browsed this product while logged OUT\n✅  I used a private / incognito window\n✅  The price matches what a guest sees\n✅  I understand payments are non-refundable",
                  style: TextStyle(fontSize: 13, height: 1.7, color: Color(0xFF344054))),
              SizedBox(height: 12),
              Text("Submitting a personalised price is a violation and may result in removal with no refund.",
                  style: TextStyle(fontSize: 12, color: Color(0xFFB42318), fontWeight: FontWeight.w600)),
            ]),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text("Cancel")),
            ElevatedButton(onPressed: () => Navigator.pop(ctx, true), child: const Text("Price is correct — Pay")),
          ],
        ));
    if (confirmed != true) return;

    setState(() => isBusy = true);
    try {
      final response = await LobbyService.initializeItemPayment(widget.token, itemId: itemId);
      final authUrl = response["authorization_url"]?.toString();
      if (authUrl != null && authUrl.isNotEmpty) {
        final launched = await launchUrl(Uri.parse(authUrl),
            mode: LaunchMode.platformDefault, webOnlyWindowName: "_blank");
        _startPolling();
        showMessage(launched
            ? "Paystack opened. Come back and tap \"Verify payment\" once done."
            : "Could not open Paystack. Try again.");
      }
      await loadAll();
    } catch (e) { showMessage(e.toString()); }
    finally { if (mounted) setState(() => isBusy = false); }
  }

  Future<void> verifyItemPayment(String reference) async {
    setState(() => isBusy = true);
    try {
      final response = await LobbyService.verifyItemPayment(widget.token, reference: reference);
      await loadAll();
      showMessage(response["payment_status"]?.toString() == "paid"
          ? "Item locked in!" : response["message"]?.toString() ?? "Not confirmed yet.",
          isSuccess: response["payment_status"]?.toString() == "paid");
    } catch (e) { showMessage(e.toString()); }
    finally { if (mounted) setState(() => isBusy = false); }
  }

  void showMessage(String message, {bool isSuccess = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Row(children: [
        Icon(isSuccess ? Icons.check_circle_outline : Icons.info_outline, color: Colors.white, size: 18),
        const SizedBox(width: 8),
        Expanded(child: Text(message)),
      ]),
      backgroundColor: isSuccess ? const Color(0xFF027A48) : const Color(0xFF344054),
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      margin: const EdgeInsets.all(16),
      duration: Duration(seconds: isSuccess ? 3 : 5),
    ));
  }

  // ── Helpers ──────────────────────────────────────────────────────────────

  Widget _pill(String v) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
    decoration: BoxDecoration(color: const Color(0xFFF9FAFB),
        borderRadius: BorderRadius.circular(999), border: Border.all(color: const Color(0xFFE4E7EC))),
    child: Text(v, style: const TextStyle(color: Color(0xFF344054), fontWeight: FontWeight.w600, fontSize: 13)),
  );

  Widget _colorBox(String t, {required Color bg, required Color border, required Color fg}) =>
      Container(width: double.infinity, padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(14), border: Border.all(color: border)),
        child: Text(t, style: TextStyle(color: fg, fontWeight: FontWeight.w600, height: 1.5)));

  Widget _warnBox(String t) => _colorBox(t, bg: const Color(0xFFFFF7E6), border: const Color(0xFFFAC515), fg: const Color(0xFF7A2E0E));
  Widget _okBox(String t)   => _colorBox(t, bg: const Color(0xFFECFDF3), border: const Color(0xFF4BB543), fg: const Color(0xFF027A48));
  Widget _infoBox(String t) => _colorBox(t, bg: const Color(0xFFEFF8FF), border: const Color(0xFF53B1FD), fg: const Color(0xFF175CD3));

  Widget _empty(String msg, {IconData icon = Icons.inbox_outlined}) => Container(
    width: double.infinity, padding: const EdgeInsets.all(28),
    decoration: BoxDecoration(color: const Color(0xFFF9FAFB),
        borderRadius: BorderRadius.circular(18), border: Border.all(color: const Color(0xFFE4E7EC))),
    child: Column(children: [
      Icon(icon, size: 36, color: const Color(0xFFD0D5DD)), const SizedBox(height: 12),
      Text(msg, textAlign: TextAlign.center, style: const TextStyle(color: Color(0xFF667085), fontSize: 14)),
    ]),
  );

  Widget _statChip(String label, String value) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
    decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(18), border: Border.all(color: Colors.white.withValues(alpha: 0.18))),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: const TextStyle(fontSize: 11, color: Color(0xFFEAF7EF), fontWeight: FontWeight.w600)),
      const SizedBox(height: 4),
      Text(value, style: const TextStyle(fontSize: 15, color: Colors.white, fontWeight: FontWeight.w800)),
    ]),
  );

  Widget _paymentBadge(String s) {
    Color bg, border; IconData icon; String label;
    switch (s) {
      case "paid":      bg = const Color(0xFFECFDF3); border = const Color(0xFF4BB543); icon = Icons.lock_outline; label = "Paid & Locked"; break;
      case "pending":   bg = const Color(0xFFFFFAEB); border = const Color(0xFFF79009); icon = Icons.hourglass_bottom_outlined; label = "Pending"; break;
      case "failed":    bg = const Color(0xFFFEF3F2); border = const Color(0xFFF04438); icon = Icons.error_outline; label = "Failed"; break;
      case "abandoned": bg = const Color(0xFFFEF3F2); border = const Color(0xFFF04438); icon = Icons.cancel_outlined; label = "Abandoned"; break;
      default:          bg = const Color(0xFFF2F4F7); border = const Color(0xFFD0D5DD); icon = Icons.radio_button_unchecked; label = "Unpaid";
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(999), border: Border.all(color: border)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 13, color: const Color(0xFF344054)), const SizedBox(width: 4),
        Text(label, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: Color(0xFF344054))),
      ]),
    );
  }

  String _prettyStatus(String s) => const {
    "triggered": "Target reached", "processing": "Processing",
    "in_transit": "In transit", "completed": "Delivered", "open": "Open",
  }[s] ?? s;

  String _statusDesc(String s) => const {
    "triggered": "Vault hit its target. Admin is preparing the order.",
    "processing": "Admin is placing the group order now.",
    "in_transit": "Your order is on its way!",
    "completed": "Delivered! Thank you for using UniCart.",
  }[s] ?? "Status update.";

  Color _statusBg(String s) => const {
    "triggered": Color(0xFFFFF7E6), "processing": Color(0xFFEFF8FF),
    "in_transit": Color(0xFFF4F3FF), "completed": Color(0xFFECFDF3),
  }[s] ?? const Color(0xFFF9FAFB);

  Color _statusBorder(String s) => const {
    "triggered": Color(0xFFFAC515), "processing": Color(0xFF53B1FD),
    "in_transit": Color(0xFF9E77ED), "completed": Color(0xFF4BB543),
  }[s] ?? const Color(0xFFE4E7EC);

  IconData _statusIcon(String s) => const {
    "triggered": Icons.flag_outlined, "processing": Icons.inventory_2_outlined,
    "in_transit": Icons.local_shipping_outlined, "completed": Icons.check_circle_outline,
  }[s] ?? Icons.info_outline;

  Widget _statusBadge(String s) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
    decoration: BoxDecoration(color: _statusBg(s), borderRadius: BorderRadius.circular(999), border: Border.all(color: _statusBorder(s))),
    child: Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(_statusIcon(s), size: 15, color: const Color(0xFF344054)), const SizedBox(width: 6),
      Text(_prettyStatus(s), style: const TextStyle(fontWeight: FontWeight.w700, color: Color(0xFF344054), fontSize: 13)),
    ]),
  );

  Widget _progressRow(String s) {
    const statuses = ["triggered", "processing", "in_transit", "completed"];
    final idx = statuses.indexOf(s);
    return Wrap(spacing: 8, runSpacing: 8, children: List.generate(statuses.length, (i) {
      final done = idx >= i;
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
            color: done ? const Color(0xFFECFDF3) : const Color(0xFFF2F4F7),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: done ? const Color(0xFF4BB543) : const Color(0xFFD0D5DD))),
        child: Text(_prettyStatus(statuses[i]), style: TextStyle(
            fontSize: 12, fontWeight: FontWeight.w700,
            color: done ? const Color(0xFF027A48) : const Color(0xFF667085))),
      );
    }));
  }

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final screenWidth = MediaQuery.of(context).size.width;
    final isWide = screenWidth > 600;
    final hPad = isWide ? 20.0 : 14.0;

    final progress = (lobby == null || lobby!.targetItemAmount == 0)
        ? 0.0 : (lobby!.currentItemAmount / lobby!.targetItemAmount).clamp(0.0, 1.0);

    final isVerified = meData?["is_student_verified"] == true;
    final isAdmin    = meData?["is_admin"] == true;
    final studentEmail = meData?["student_pau_email"]?.toString();
    final accountEmail = meData?["email"]?.toString() ?? "Unknown";

    final myItemCount       = myItemsData?["item_count"] ?? 0;
    final myTotalItemAmount = myItemsData?["total_item_amount"] ?? 0;

    final rawItems = (myItemsData?["items"] as List?) ?? [];
    final activeItems = rawItems
        .where((i) => i is Map<String, dynamic> && i["is_active"] == true)
        .cast<Map<String, dynamic>>().toList();

    final historyBatches = ((myHistoryData?["batches"] as List?) ?? []).cast<Map<String, dynamic>>();
    final hasJoined        = mainDetailsData?["has_joined"] == true;
    final hasPendingPayment = mainDetailsData?["has_pending_payment"] == true;
    final entryFeeAmount   = mainDetailsData?["entry_fee_amount"] ?? 2000;

    return Scaffold(
      appBar: AppBar(
        title: const Text("UniCart", style: TextStyle(fontWeight: FontWeight.w800)),
        actions: [
          if (_pollTimer != null)
            const Padding(padding: EdgeInsets.only(right: 4),
              child: Center(child: SizedBox(width: 16, height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2)))),
          IconButton(onPressed: isLoading ? null : loadAll, icon: const Icon(Icons.refresh)),
          IconButton(onPressed: logout, icon: const Icon(Icons.logout)),
        ],
      ),
      body: isLoading
          ? const Center(child: CircularProgressIndicator())
          : SafeArea(
              child: RefreshIndicator(
                onRefresh: loadAll,
                child: ListView(
                  physics: const AlwaysScrollableScrollPhysics(),
                  padding: EdgeInsets.symmetric(horizontal: hPad, vertical: 14),
                  children: [

                    // Error
                    if (error != null) ...[
                      Container(
                        padding: const EdgeInsets.all(14),
                        decoration: BoxDecoration(color: const Color(0xFFFEF3F2),
                            borderRadius: BorderRadius.circular(14), border: Border.all(color: const Color(0xFFFECACA))),
                        child: Row(children: [
                          const Icon(Icons.error_outline, color: Color(0xFFB42318), size: 18),
                          const SizedBox(width: 8),
                          Expanded(child: Text(error!, style: const TextStyle(color: Color(0xFFB42318), fontWeight: FontWeight.w600))),
                        ]),
                      ),
                      const SizedBox(height: 14),
                    ],

                    // ── Hero ──────────────────────────────────────────────
                    Container(
                      padding: EdgeInsets.all(isWide ? 22 : 18),
                      decoration: BoxDecoration(
                        gradient: const LinearGradient(colors: [Color(0xFF1F7A4C), Color(0xFF2E8B57)],
                            begin: Alignment.topLeft, end: Alignment.bottomRight),
                        borderRadius: BorderRadius.circular(24),
                      ),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text("Campus group buying", style: TextStyle(
                            fontSize: isWide ? 26 : 22, fontWeight: FontWeight.w800, color: Colors.white, height: 1.2)),
                        const SizedBox(height: 6),
                        const Text("Add items, pay for them, hit the vault target together.",
                            style: TextStyle(fontSize: 13, color: Color(0xFFEAF7EF))),
                        const SizedBox(height: 16),
                        Wrap(spacing: 8, runSpacing: 8, children: [
                          _statChip("Members", "${lobby?.memberCount ?? 0}"),
                          _statChip("Status", lobby?.status ?? "—"),
                          _statChip("My Paid Items", "$myItemCount"),
                          _statChip("My Paid Total", "₦$myTotalItemAmount"),
                        ]),
                      ]),
                    ),

                    const SizedBox(height: 16),

                    // ── Vault progress ────────────────────────────────────
                    Card(elevation: 0, child: Padding(
                      padding: const EdgeInsets.all(18),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Row(children: [
                          const Expanded(child: Text("Vault progress",
                              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF101828)))),
                          Text("${(progress * 100).toStringAsFixed(1)}%",
                              style: const TextStyle(fontWeight: FontWeight.w800, color: Color(0xFF1F7A4C), fontSize: 15)),
                        ]),
                        const SizedBox(height: 6),
                        Text("Lobby #${lobby?.lobbyId ?? "-"}", style: const TextStyle(color: Color(0xFF667085), fontSize: 13)),
                        const SizedBox(height: 12),
                        ClipRRect(borderRadius: BorderRadius.circular(999),
                            child: LinearProgressIndicator(value: progress, minHeight: 12)),
                        const SizedBox(height: 10),
                        Text("₦${lobby?.currentItemAmount ?? 0}  /  ₦${lobby?.targetItemAmount ?? 0}",
                            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: Color(0xFF101828))),
                        if (progress >= 1.0) ...[
                          const SizedBox(height: 10),
                          _okBox("🎉 Vault target reached! This batch has been triggered."),
                        ],
                      ]),
                    )),

                    const SizedBox(height: 14),

                    // ── My account ────────────────────────────────────────
                    Card(elevation: 0, child: Padding(
                      padding: const EdgeInsets.all(18),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        const Text("My account",
                            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF101828))),
                        const SizedBox(height: 14),
                        // Avatar + info row
                        Row(children: [
                          Container(
                            width: 48, height: 48,
                            decoration: BoxDecoration(color: const Color(0xFF1F7A4C), borderRadius: BorderRadius.circular(14)),
                            child: Center(child: Text(
                              accountEmail.isNotEmpty ? accountEmail[0].toUpperCase() : "?",
                              style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w800),
                            )),
                          ),
                          const SizedBox(width: 14),
                          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text(accountEmail,
                                style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13, color: Color(0xFF101828)),
                                overflow: TextOverflow.ellipsis),
                            const SizedBox(height: 6),
                            Wrap(spacing: 6, runSpacing: 4, children: [
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                decoration: BoxDecoration(
                                    color: isVerified ? const Color(0xFFECFDF3) : const Color(0xFFFEF3F2),
                                    borderRadius: BorderRadius.circular(999),
                                    border: Border.all(color: isVerified ? const Color(0xFF4BB543) : const Color(0xFFFECACA))),
                                child: Text(isVerified ? "✅ PAU Verified" : "❌ Not verified",
                                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700,
                                        color: isVerified ? const Color(0xFF027A48) : const Color(0xFFB42318))),
                              ),
                              if (isAdmin) Container(
                                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                decoration: BoxDecoration(color: const Color(0xFFF4F3FF),
                                    borderRadius: BorderRadius.circular(999), border: Border.all(color: const Color(0xFF9E77ED))),
                                child: const Text("👑 Admin",
                                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: Color(0xFF5925DC))),
                              ),
                            ]),
                            if (studentEmail != null) ...[
                              const SizedBox(height: 4),
                              Text("🎓 $studentEmail",
                                  style: const TextStyle(fontSize: 11, color: Color(0xFF667085)),
                                  overflow: TextOverflow.ellipsis),
                            ],
                          ])),
                        ]),
                        const SizedBox(height: 14),
                        SizedBox(width: double.infinity, child: ElevatedButton(
                          onPressed: isBusy ? null : openVerifyScreen,
                          child: Text(isVerified ? "Update PAU verification" : "Verify PAU email"),
                        )),
                        if (isAdmin) ...[
                          const SizedBox(height: 10),
                          SizedBox(width: double.infinity, child: OutlinedButton.icon(
                            onPressed: isBusy ? null : openAdminDashboard,
                            icon: const Icon(Icons.admin_panel_settings_outlined),
                            label: const Text("Admin dashboard"),
                          )),
                        ],
                      ]),
                    )),

                    const SizedBox(height: 14),

                    // ── Lobby access ──────────────────────────────────────
                    Card(elevation: 0, child: Padding(
                      padding: const EdgeInsets.all(18),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        const Text("Lobby access",
                            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF101828))),
                        const SizedBox(height: 6),
                        const Text("Pay the entry fee, join, and manage your spot.",
                            style: TextStyle(color: Color(0xFF667085), fontSize: 13)),
                        const SizedBox(height: 14),
                        Wrap(spacing: 10, runSpacing: 10, children: [
                          _tileSmall("Lobby ID", "#${lobby?.lobbyId ?? "-"}"),
                          _tileSmall("Status", lobby?.status ?? "Unknown"),
                          _tileSmall("Entry fee", "₦$entryFeeAmount"),
                        ]),
                        const SizedBox(height: 14),
                        if (!isVerified)
                          _warnBox("Verify your PAU email before paying to join.")
                        else if (hasJoined)
                          _okBox("✅ You're in the lobby. Add items and pay for them.")
                        else if (hasPendingPayment || pendingPaymentReference != null)
                          Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                            _infoBox("⏳ Entry fee payment pending.${pendingPaymentReference != null ? "\nRef: $pendingPaymentReference" : ""}"),
                            const SizedBox(height: 12),
                            // BIG green verify button — can't miss on mobile
                            ElevatedButton.icon(
                              style: ElevatedButton.styleFrom(
                                  backgroundColor: const Color(0xFF1F7A4C),
                                  minimumSize: const Size.fromHeight(52)),
                              onPressed: isBusy ? null : verifyEntryFeePayment,
                              icon: const Icon(Icons.verified_outlined, color: Colors.white),
                              label: const Text("I've Paid — Confirm Now",
                                  style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 15)),
                            ),
                            const SizedBox(height: 8),
                            OutlinedButton.icon(
                              onPressed: isBusy ? null : reopenPaymentLink,
                              icon: const Icon(Icons.open_in_new),
                              label: const Text("Open Paystack again"),
                            ),
                          ])
                        else
                          SizedBox(width: double.infinity, child: ElevatedButton.icon(
                            style: ElevatedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
                            onPressed: isBusy ? null : startEntryFeePayment,
                            icon: const Icon(Icons.payment),
                            label: Text("Pay ₦$entryFeeAmount to Join"),
                          )),
                        const SizedBox(height: 10),
                        SizedBox(width: double.infinity, child: OutlinedButton(
                          onPressed: isBusy || !hasJoined ? null : leaveLobby,
                          child: const Text("Leave lobby"),
                        )),
                      ]),
                    )),

                    const SizedBox(height: 14),

                    // ── Add item ──────────────────────────────────────────
                    Card(elevation: 0, child: Padding(
                      padding: const EdgeInsets.all(18),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        const Text("Add item",
                            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF101828))),
                        const SizedBox(height: 6),
                        const Text("Paste your product link and guest price. Pay to lock it into the vault.",
                            style: TextStyle(color: Color(0xFF667085), fontSize: 13)),
                        const SizedBox(height: 12),
                        // Guest price warning
                        Container(
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(color: const Color(0xFFFFF7E6),
                              borderRadius: BorderRadius.circular(12), border: Border.all(color: const Color(0xFFFAC515))),
                          child: const Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Row(children: [
                              Icon(Icons.warning_amber_rounded, size: 16, color: Color(0xFFB54708)),
                              SizedBox(width: 6),
                              Text("Use guest (logged-out) pricing",
                                  style: TextStyle(fontWeight: FontWeight.w800, fontSize: 13, color: Color(0xFF7A2E0E))),
                            ]),
                            SizedBox(height: 6),
                            Text(
                              "Log OUT of Temu before viewing the price. "
                              "Open the product in a private/incognito window. "
                              "Submit the price you see as a guest — not your personalised price.",
                              style: TextStyle(fontSize: 12, color: Color(0xFF7A2E0E), height: 1.5),
                            ),
                          ]),
                        ),
                        const SizedBox(height: 10),
                        // No-refund
                        Container(
                          padding: const EdgeInsets.all(10),
                          decoration: BoxDecoration(color: const Color(0xFFFEF3F2),
                              borderRadius: BorderRadius.circular(10), border: Border.all(color: const Color(0xFFFECACA))),
                          child: const Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Icon(Icons.gavel_outlined, size: 14, color: Color(0xFFB42318)),
                            SizedBox(width: 6),
                            Expanded(child: Text(
                              "No-Refund Policy: Item payments are non-refundable. "
                              "Items removed for violations will not be refunded.",
                              style: TextStyle(fontSize: 11, color: Color(0xFFB42318), fontWeight: FontWeight.w600, height: 1.5),
                            )),
                          ]),
                        ),
                        const SizedBox(height: 14),
                        TextField(
                          controller: itemLinkController,
                          decoration: const InputDecoration(
                            labelText: "Product link",
                            prefixIcon: Icon(Icons.link),
                            helperText: "From a guest / logged-out browser session",
                          ),
                        ),
                        const SizedBox(height: 12),
                        TextField(
                          controller: itemAmountController,
                          keyboardType: const TextInputType.numberWithOptions(decimal: true),
                          decoration: const InputDecoration(
                            labelText: "Guest price (₦)",
                            prefixIcon: Icon(Icons.payments_outlined),
                          ),
                        ),
                        const SizedBox(height: 14),
                        SizedBox(width: double.infinity, child: ElevatedButton.icon(
                          style: ElevatedButton.styleFrom(minimumSize: const Size.fromHeight(50)),
                          onPressed: isBusy || !hasJoined ? null : addItem,
                          icon: const Icon(Icons.add),
                          label: const Text("Add item"),
                        )),
                        if (!hasJoined) Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: Text("Join the lobby first to add items.",
                              style: TextStyle(color: Colors.grey.shade500, fontSize: 12)),
                        ),
                      ]),
                    )),

                    const SizedBox(height: 14),

                    // ── My active items ───────────────────────────────────
                    Card(elevation: 0, child: Padding(
                      padding: const EdgeInsets.all(18),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Row(children: [
                          const Expanded(child: Text("My active items",
                              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF101828)))),
                          if (activeItems.isNotEmpty)
                            _pill("${activeItems.length} item${activeItems.length == 1 ? "" : "s"}"),
                        ]),
                        const SizedBox(height: 6),
                        const Text("Pay to lock items in. Unpaid items are removed when the batch triggers.",
                            style: TextStyle(color: Color(0xFF667085), fontSize: 13)),
                        const SizedBox(height: 14),
                        if (activeItems.isEmpty)
                          _empty("No active items yet.\nAdd an item above and pay for it to lock it in.",
                              icon: Icons.shopping_bag_outlined)
                        else
                          ...activeItems.map((item) {
                            final itemId   = item["item_id"] as int;
                            final itemLink = item["item_link"]?.toString() ?? "";
                            final itemAmt  = item["item_amount"] ?? 0;
                            final pStat    = item["item_payment_status"]?.toString() ?? "unpaid";
                            final isLocked = item["is_locked"] == true;
                            final isPaid   = item["is_paid"] == true;
                            final ref      = item["item_payment_reference"]?.toString();

                            return Container(
                              margin: const EdgeInsets.only(bottom: 12),
                              padding: const EdgeInsets.all(14),
                              decoration: BoxDecoration(
                                color: isPaid ? const Color(0xFFF0FDF4)
                                    : pStat == "pending" ? const Color(0xFFFFFBEB) : const Color(0xFFF9FAFB),
                                borderRadius: BorderRadius.circular(16),
                                border: Border.all(
                                    color: isPaid ? const Color(0xFF86EFAC)
                                        : pStat == "pending" ? const Color(0xFFFDE68A) : const Color(0xFFE4E7EC)),
                              ),
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                Text(itemLink,
                                    style: const TextStyle(fontWeight: FontWeight.w700, color: Color(0xFF101828), fontSize: 13),
                                    maxLines: 2, overflow: TextOverflow.ellipsis),
                                const SizedBox(height: 8),
                                Wrap(spacing: 8, runSpacing: 6, children: [
                                  _pill("₦$itemAmt"),
                                  _paymentBadge(pStat),
                                ]),
                                const SizedBox(height: 10),
                                Wrap(spacing: 8, runSpacing: 8, children: [
                                  if (!isLocked) OutlinedButton.icon(
                                    onPressed: isBusy ? null : () => payForItem(itemId),
                                    icon: const Icon(Icons.payment_outlined, size: 15),
                                    label: const Text("Pay for item", style: TextStyle(fontSize: 13)),
                                  ),
                                  if (pStat == "pending" && ref != null) ElevatedButton.icon(
                                    onPressed: isBusy ? null : () => verifyItemPayment(ref),
                                    icon: const Icon(Icons.verified_outlined, size: 15),
                                    label: const Text("Verify payment", style: TextStyle(fontSize: 13)),
                                  ),
                                  if (!isLocked) OutlinedButton.icon(
                                    onPressed: isBusy ? null : () => removeItem(itemId),
                                    icon: const Icon(Icons.delete_outline, size: 15),
                                    label: const Text("Remove", style: TextStyle(fontSize: 13)),
                                  ),
                                  if (isPaid) Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                                    decoration: BoxDecoration(color: const Color(0xFFECFDF3),
                                        borderRadius: BorderRadius.circular(999), border: Border.all(color: const Color(0xFF4BB543))),
                                    child: const Row(mainAxisSize: MainAxisSize.min, children: [
                                      Icon(Icons.lock_outline, size: 13, color: Color(0xFF027A48)), SizedBox(width: 4),
                                      Text("Locked", style: TextStyle(color: Color(0xFF027A48), fontWeight: FontWeight.w700, fontSize: 12)),
                                    ]),
                                  ),
                                ]),
                              ]),
                            );
                          }),
                      ]),
                    )),

                    const SizedBox(height: 14),

                    // ── Batch history ─────────────────────────────────────
                    Card(elevation: 0, child: Padding(
                      padding: const EdgeInsets.all(18),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        const Text("My batch history",
                            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: Color(0xFF101828))),
                        const SizedBox(height: 6),
                        const Text("Track past and current batches you've participated in.",
                            style: TextStyle(color: Color(0xFF667085), fontSize: 13)),
                        const SizedBox(height: 14),
                        if (historyBatches.isEmpty)
                          _empty("No batch history yet.\nCompleted batches will appear here.", icon: Icons.history_outlined)
                        else
                          ...historyBatches.map((batch) {
                            final rawStatus = batch["status"]?.toString() ?? "";
                            return Container(
                              margin: const EdgeInsets.only(bottom: 12),
                              padding: const EdgeInsets.all(14),
                              decoration: BoxDecoration(color: const Color(0xFFF9FAFB),
                                  borderRadius: BorderRadius.circular(16), border: Border.all(color: const Color(0xFFE4E7EC))),
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                Row(children: [
                                  Expanded(child: Text("Batch #${batch["lobby_id"]}",
                                      style: const TextStyle(fontWeight: FontWeight.w700, color: Color(0xFF101828)))),
                                  _statusBadge(rawStatus),
                                ]),
                                const SizedBox(height: 8),
                                Text(_statusDesc(rawStatus),
                                    style: const TextStyle(color: Color(0xFF667085), fontSize: 13)),
                                const SizedBox(height: 10),
                                _progressRow(rawStatus),
                                const SizedBox(height: 10),
                                Wrap(spacing: 8, runSpacing: 8, children: [
                                  _pill("Final: ₦${batch["final_item_amount"]} / ₦${batch["target_item_amount"]}"),
                                  _pill("My items: ${batch["my_item_count"]}"),
                                  _pill("My total: ₦${batch["my_total_item_amount"]}"),
                                ]),
                              ]),
                            );
                          }),
                      ]),
                    )),

                    const SizedBox(height: 32),
                  ],
                ),
              ),
            ),
    );
  }

  Widget _tileSmall(String label, String value) => Container(
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(color: const Color(0xFFF9FAFB),
        borderRadius: BorderRadius.circular(14), border: Border.all(color: const Color(0xFFE4E7EC))),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: const TextStyle(fontSize: 11, color: Color(0xFF667085), fontWeight: FontWeight.w600)),
      const SizedBox(height: 4),
      Text(value, style: const TextStyle(fontSize: 14, color: Color(0xFF101828), fontWeight: FontWeight.w800)),
    ]),
  );
}