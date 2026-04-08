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

  // Polling timer — refreshes every 15s when a payment is pending
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
    if (!silent) {
      setState(() {
        isLoading = true;
        error = null;
      });
    }

    try {
      Map<String, dynamic>? me;
      Map<String, dynamic>? myItems;
      Map<String, dynamic>? myHistory;
      Map<String, dynamic>? details;

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
            currentItemAmount: details["current_item_amount"] as int? ?? 0,
            targetItemAmount: details["target_item_amount"] as int? ?? 0,
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

      // Auto-poll while a payment is pending to catch Paystack webhook updates
      final hasPending = details?["has_pending_payment"] == true ||
          pendingPaymentReference != null;
      final hasItemPending = (myItems?["items"] as List? ?? []).any(
        (i) => i is Map && i["item_payment_status"] == "pending",
      );

      if (hasPending || hasItemPending) {
        _startPolling();
      } else {
        _stopPolling();
      }
    } catch (e) {
      if (!silent) {
        setState(() => error = e.toString());
      }
    } finally {
      if (mounted && !silent) {
        setState(() => isLoading = false);
      }
    }
  }

  Future<void> logout() async {
    _stopPolling();
    await SessionService.clearToken();
    if (!mounted) return;
    Navigator.pushAndRemoveUntil(
      context,
      MaterialPageRoute(builder: (_) => const LoginScreen()),
      (route) => false,
    );
  }

  Future<void> openVerifyScreen() async {
    final result = await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => VerifyPauScreen(token: widget.token)),
    );
    if (result == true) await loadAll();
  }

  Future<void> openAdminDashboard() async {
    await Navigator.push(
      context,
      MaterialPageRoute(
          builder: (_) => AdminDashboardScreen(token: widget.token)),
    );
    await loadAll();
  }

  Future<void> startEntryFeePayment() async {
    setState(() => isBusy = true);
    try {
      final response = await LobbyService.initializeEntryFeePayment(widget.token);
      final reference = response["reference"]?.toString();
      final authorizationUrl = response["authorization_url"]?.toString();

      setState(() {
        pendingPaymentReference = reference;
        pendingPaymentUrl = authorizationUrl;
      });

      if (authorizationUrl != null && authorizationUrl.isNotEmpty) {
        final launched = await launchUrl(
          Uri.parse(authorizationUrl),
          webOnlyWindowName: "_blank",
        );
        if (launched) {
          _startPolling(); // Start polling after payment page opens
          showMessage(
            "Payment page opened. Complete your payment — we'll detect it automatically.",
            isSuccess: true,
          );
        } else {
          showMessage("Payment link ready but couldn't open automatically. Tap 'Open again'.");
        }
      }
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  Future<void> verifyEntryFeePayment() async {
    final reference = pendingPaymentReference;
    if (reference == null || reference.isEmpty) {
      showMessage("No pending payment reference found.");
      return;
    }
    setState(() => isBusy = true);
    try {
      final response = await LobbyService.verifyEntryFeePayment(
        widget.token, reference: reference,
      );
      if (response["status"]?.toString() == "success") {
        setState(() {
          pendingPaymentReference = null;
          pendingPaymentUrl = null;
        });
        _stopPolling();
        showMessage("Payment confirmed! You've joined the lobby.", isSuccess: true);
      } else {
        showMessage(response["message"]?.toString() ?? "Payment not confirmed yet.");
      }
      await loadAll();
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  Future<void> reopenPaymentLink() async {
    if (pendingPaymentUrl == null || pendingPaymentUrl!.isEmpty) {
      showMessage("No payment link available.");
      return;
    }
    final launched = await launchUrl(
      Uri.parse(pendingPaymentUrl!),
      webOnlyWindowName: "_blank",
    );
    if (!launched) showMessage("Could not open payment link.");
  }

  Future<void> leaveLobby() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text("Leave lobby?"),
        content: const Text(
          "You will need to pay the entry fee again to rejoin. Unpaid items will be removed.",
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text("Cancel"),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text("Leave"),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    setState(() => isBusy = true);
    try {
      final response = await LobbyService.leaveLobby(widget.token);
      await loadAll();
      showMessage(response["message"]?.toString() ?? "Left lobby");
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  Future<void> addItem() async {
    final amountRaw = double.tryParse(itemAmountController.text.trim());
    final amount = amountRaw != null ? double.parse(amountRaw.toStringAsFixed(2)) : null;
    final itemLink = itemLinkController.text.trim();
    if (itemLink.isEmpty || amount == null || amount <= 0) {
      showMessage("Enter a valid item link and amount.");
      return;
    }
    setState(() => isBusy = true);
    try {
      final response = await LobbyService.addItem(
        widget.token, itemLink: itemLink, itemAmount: amount.toInt(),
      );
      itemLinkController.clear();
      itemAmountController.clear();
      await loadAll();
      showMessage(
        response["message"]?.toString() ?? "Item added. Pay for it to count toward the goal.",
        isSuccess: true,
      );
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  Future<void> removeItem(int itemId) async {
    setState(() => isBusy = true);
    try {
      final response = await LobbyService.removeItem(widget.token, itemId: itemId);
      await loadAll();
      showMessage(response["message"]?.toString() ?? "Item removed.");
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  Future<void> payForItem(int itemId) async {
    // ── Guest price confirmation before payment ────────────────────────────────
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.warning_amber_rounded, color: Color(0xFFB54708)),
            SizedBox(width: 8),
            Text("Confirm guest price",
                style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
          ],
        ),
        content: const Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              "Before paying, confirm that the price you entered is the "
              "guest (logged-out) price from Temu or the relevant platform.",
              style: TextStyle(fontSize: 14, height: 1.5),
            ),
            SizedBox(height: 14),
            Text("Checklist:", style: TextStyle(fontWeight: FontWeight.w800)),
            SizedBox(height: 6),
            Text(
              "✅  I browsed this product while logged OUT"
              "✅  I used a private / guest account"
              "✅  The price I entered matches what a guest sees"
              "✅  I understand payments are non-refundable",
              style: TextStyle(fontSize: 13, height: 1.6, color: Color(0xFF344054)),
            ),
            SizedBox(height: 14),
            Text(
              "Submitting a personalised price is a policy violation "
              "and may result in removal with no refund.",
              style: TextStyle(
                  fontSize: 12,
                  color: Color(0xFFB42318),
                  fontWeight: FontWeight.w600),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text("Cancel"),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text("Yes, price is correct — Pay"),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    setState(() => isBusy = true);
    try {
      final response = await LobbyService.initializeItemPayment(
        widget.token, itemId: itemId,
      );
      final authorizationUrl = response["authorization_url"]?.toString();
      if (authorizationUrl != null && authorizationUrl.isNotEmpty) {
        final launched = await launchUrl(
          Uri.parse(authorizationUrl),
          webOnlyWindowName: "_blank",
        );
        if (launched) {
          _startPolling(); // Poll for webhook updates
          showMessage(
            "Payment page opened. Tap 'Verify' after completing payment.",
            isSuccess: true,
          );
        } else {
          showMessage("Payment link ready but couldn't open automatically.");
        }
      }
      await loadAll();
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  Future<void> verifyItemPayment(String reference) async {
    setState(() => isBusy = true);
    try {
      final response = await LobbyService.verifyItemPayment(
        widget.token, reference: reference,
      );
      await loadAll();
      final status = response["payment_status"]?.toString();
      if (status == "paid") {
        showMessage("Item payment confirmed and locked!", isSuccess: true);
      } else {
        showMessage(response["message"]?.toString() ?? "Payment not confirmed yet.");
      }
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  void showMessage(String message, {bool isSuccess = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            Icon(
              isSuccess ? Icons.check_circle_outline : Icons.info_outline,
              color: Colors.white,
              size: 18,
            ),
            const SizedBox(width: 8),
            Expanded(child: Text(message)),
          ],
        ),
        backgroundColor: isSuccess
            ? const Color(0xFF027A48)
            : const Color(0xFF344054),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        margin: const EdgeInsets.all(16),
        duration: Duration(seconds: isSuccess ? 3 : 4),
      ),
    );
  }

  // ─── Widget helpers ─────────────────────────────────────────────────────────

  Widget statChip(String label, String value) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: Colors.white.withValues(alpha: 0.18)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: const TextStyle(
                  fontSize: 12, color: Color(0xFFEAF7EF), fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Text(value,
              style: const TextStyle(
                  fontSize: 16, color: Colors.white, fontWeight: FontWeight.w800)),
        ],
      ),
    );
  }

  Widget infoPill(String value) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xFFF9FAFB),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: const Color(0xFFE4E7EC)),
      ),
      child: Text(value,
          style: const TextStyle(
              color: Color(0xFF344054), fontWeight: FontWeight.w600, fontSize: 13)),
    );
  }

  Widget detailTile(String label, String value) {
    return Container(
      width: 180,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFFF9FAFB),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: const Color(0xFFE4E7EC)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: const TextStyle(
                  fontSize: 12, color: Color(0xFF667085), fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Text(value,
              style: const TextStyle(
                  fontSize: 16, color: Color(0xFF101828), fontWeight: FontWeight.w800)),
        ],
      ),
    );
  }

  Widget buildItemPaymentBadge(String paymentStatus) {
    Color bg; Color border; IconData icon; String label;
    switch (paymentStatus) {
      case "paid":
        bg = const Color(0xFFECFDF3); border = const Color(0xFF4BB543);
        icon = Icons.lock_outline; label = "Paid & Locked"; break;
      case "pending":
        bg = const Color(0xFFFFFAEB); border = const Color(0xFFF79009);
        icon = Icons.hourglass_bottom_outlined; label = "Payment Pending"; break;
      case "failed":
        bg = const Color(0xFFFEF3F2); border = const Color(0xFFF04438);
        icon = Icons.error_outline; label = "Payment Failed"; break;
      case "abandoned":
        bg = const Color(0xFFFEF3F2); border = const Color(0xFFF04438);
        icon = Icons.cancel_outlined; label = "Abandoned"; break;
      default:
        bg = const Color(0xFFF2F4F7); border = const Color(0xFFD0D5DD);
        icon = Icons.radio_button_unchecked; label = "Unpaid";
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: bg, borderRadius: BorderRadius.circular(999),
        border: Border.all(color: border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: const Color(0xFF344054)),
          const SizedBox(width: 5),
          Text(label,
              style: const TextStyle(
                  fontSize: 12, fontWeight: FontWeight.w700, color: Color(0xFF344054))),
        ],
      ),
    );
  }

  String prettyBatchStatus(String s) {
    switch (s) {
      case "triggered": return "Target reached";
      case "processing": return "Processing";
      case "in_transit": return "In transit";
      case "completed": return "Delivered";
      case "open": return "Open";
      default: return s;
    }
  }

  String batchStatusDescription(String s) {
    switch (s) {
      case "triggered": return "Your batch reached the target and is waiting for the lead buyer.";
      case "processing": return "The lead buyer is currently preparing and placing this order.";
      case "in_transit": return "Your shared order is on the way.";
      case "completed": return "This batch has been completed and delivered.";
      default: return "Batch status available.";
    }
  }

  Color statusBg(String s) {
    switch (s) {
      case "triggered": return const Color(0xFFFFF7E6);
      case "processing": return const Color(0xFFEFF8FF);
      case "in_transit": return const Color(0xFFF4F3FF);
      case "completed": return const Color(0xFFECFDF3);
      default: return const Color(0xFFF9FAFB);
    }
  }

  Color statusBorder(String s) {
    switch (s) {
      case "triggered": return const Color(0xFFFAC515);
      case "processing": return const Color(0xFF53B1FD);
      case "in_transit": return const Color(0xFF9E77ED);
      case "completed": return const Color(0xFF4BB543);
      default: return const Color(0xFFE4E7EC);
    }
  }

  IconData statusIcon(String s) {
    switch (s) {
      case "triggered": return Icons.flag_outlined;
      case "processing": return Icons.inventory_2_outlined;
      case "in_transit": return Icons.local_shipping_outlined;
      case "completed": return Icons.check_circle_outline;
      default: return Icons.info_outline;
    }
  }

  Widget buildStatusBadge(String rawStatus) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: statusBg(rawStatus), borderRadius: BorderRadius.circular(999),
        border: Border.all(color: statusBorder(rawStatus)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(statusIcon(rawStatus), size: 16, color: const Color(0xFF344054)),
          const SizedBox(width: 6),
          Text(prettyBatchStatus(rawStatus),
              style: const TextStyle(
                  fontWeight: FontWeight.w700, color: Color(0xFF344054))),
        ],
      ),
    );
  }

  Widget buildBatchProgressRow(String rawStatus) {
    final statuses = ["triggered", "processing", "in_transit", "completed"];
    final currentIndex = statuses.indexOf(rawStatus);
    return Wrap(
      spacing: 10, runSpacing: 10,
      children: List.generate(statuses.length, (index) {
        final isDone = currentIndex >= index;
        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: isDone ? const Color(0xFFECFDF3) : const Color(0xFFF2F4F7),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(
              color: isDone ? const Color(0xFF4BB543) : const Color(0xFFD0D5DD),
            ),
          ),
          child: Text(
            prettyBatchStatus(statuses[index]),
            style: TextStyle(
              fontWeight: FontWeight.w700,
              color: isDone ? const Color(0xFF027A48) : const Color(0xFF667085),
            ),
          ),
        );
      }),
    );
  }

  Widget _colorBox(String text,
      {required Color bg, required Color border, required Color textColor}) {
    return Container(
      width: double.infinity, padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: bg, borderRadius: BorderRadius.circular(16),
        border: Border.all(color: border),
      ),
      child: Text(text, style: TextStyle(color: textColor, fontWeight: FontWeight.w700)),
    );
  }

  Widget _warningBox(String t) => _colorBox(t,
      bg: const Color(0xFFFFF7E6), border: const Color(0xFFFAC515), textColor: const Color(0xFF7A2E0E));
  Widget _successBox(String t) => _colorBox(t,
      bg: const Color(0xFFECFDF3), border: const Color(0xFF4BB543), textColor: const Color(0xFF027A48));
  Widget _infoBox(String t) => _colorBox(t,
      bg: const Color(0xFFEFF8FF), border: const Color(0xFF53B1FD), textColor: const Color(0xFF175CD3));

  Widget _emptyState(String message, {IconData icon = Icons.inbox_outlined}) {
    return Container(
      width: double.infinity, padding: const EdgeInsets.all(28),
      decoration: BoxDecoration(
        color: const Color(0xFFF9FAFB), borderRadius: BorderRadius.circular(18),
        border: Border.all(color: const Color(0xFFE4E7EC)),
      ),
      child: Column(
        children: [
          Icon(icon, size: 36, color: const Color(0xFFD0D5DD)),
          const SizedBox(height: 12),
          Text(message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: Color(0xFF667085), fontSize: 14)),
        ],
      ),
    );
  }

  // ─── Build ──────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final progress = (lobby == null || lobby!.targetItemAmount == 0)
        ? 0.0
        : (lobby!.currentItemAmount / lobby!.targetItemAmount).clamp(0.0, 1.0);

    final isVerified = meData?["is_student_verified"] == true;
    final isAdmin = meData?["is_admin"] == true;
    final studentEmail = meData?["student_pau_email"]?.toString();
    final accountEmail = meData?["email"]?.toString() ?? "Unknown";

    final myItemCount = myItemsData?["item_count"] ?? 0;
    final myTotalItemAmount = myItemsData?["total_item_amount"] ?? 0;

    final rawItems = (myItemsData?["items"] as List?) ?? [];
    final activeItems = rawItems
        .where((i) => i is Map<String, dynamic> && i["is_active"] == true)
        .cast<Map<String, dynamic>>()
        .toList();

    final historyBatches =
        ((myHistoryData?["batches"] as List?) ?? []).cast<Map<String, dynamic>>();

    final hasJoined = mainDetailsData?["has_joined"] == true;
    final hasPendingPayment = mainDetailsData?["has_pending_payment"] == true;
    final entryFeeAmount = mainDetailsData?["entry_fee_amount"] ?? 2000;

    return Scaffold(
      appBar: AppBar(
        title: const Text("UniCart", style: TextStyle(fontWeight: FontWeight.w800)),
        actions: [
          if (_pollTimer != null)
            const Padding(
              padding: EdgeInsets.only(right: 4),
              child: Center(
                child: SizedBox(
                  width: 16, height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            ),
          IconButton(
            onPressed: isLoading ? null : loadAll,
            icon: const Icon(Icons.refresh),
          ),
          IconButton(onPressed: logout, icon: const Icon(Icons.logout)),
        ],
      ),
      body: isLoading
          ? const Center(child: CircularProgressIndicator())
          : SafeArea(
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 1080),
                  child: RefreshIndicator(
                    onRefresh: loadAll,
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: ListView(
                        physics: const AlwaysScrollableScrollPhysics(),
                        children: [
                          // Error banner
                          if (error != null)
                            Container(
                              margin: const EdgeInsets.only(bottom: 16),
                              padding: const EdgeInsets.all(14),
                              decoration: BoxDecoration(
                                color: const Color(0xFFFEF3F2),
                                borderRadius: BorderRadius.circular(16),
                                border: Border.all(color: const Color(0xFFFECACA)),
                              ),
                              child: Row(
                                children: [
                                  const Icon(Icons.error_outline,
                                      color: Color(0xFFB42318), size: 18),
                                  const SizedBox(width: 8),
                                  Expanded(
                                    child: Text(error!,
                                        style: const TextStyle(
                                            color: Color(0xFFB42318),
                                            fontWeight: FontWeight.w600)),
                                  ),
                                ],
                              ),
                            ),

                          // Hero banner
                          Container(
                            padding: const EdgeInsets.all(22),
                            decoration: BoxDecoration(
                              gradient: const LinearGradient(
                                colors: [Color(0xFF1F7A4C), Color(0xFF2E8B57)],
                                begin: Alignment.topLeft,
                                end: Alignment.bottomRight,
                              ),
                              borderRadius: BorderRadius.circular(28),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text("Campus group buying made easier",
                                    style: TextStyle(
                                        fontSize: 26, height: 1.2,
                                        fontWeight: FontWeight.w800, color: Colors.white)),
                                const SizedBox(height: 8),
                                const Text(
                                    "Add items, pay for them, and help the vault hit its target.",
                                    style: TextStyle(fontSize: 14, color: Color(0xFFEAF7EF))),
                                const SizedBox(height: 8),
                                const Text(
                                    "Only paid items count toward the vault goal.",
                                    style: TextStyle(
                                        fontSize: 12, color: Color(0xFFDFF5E7),
                                        fontWeight: FontWeight.w600)),
                                const SizedBox(height: 20),
                                Wrap(
                                  spacing: 10, runSpacing: 10,
                                  children: [
                                    statChip("Members", "${lobby?.memberCount ?? 0}"),
                                    statChip("Status", lobby?.status ?? "Unknown"),
                                    statChip("My Paid Items", "$myItemCount"),
                                    statChip("My Paid Total", "₦$myTotalItemAmount"),
                                  ],
                                ),
                              ],
                            ),
                          ),

                          const SizedBox(height: 18),

                          // Vault progress
                          Card(
                            elevation: 0,
                            child: Padding(
                              padding: const EdgeInsets.all(22),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      const Expanded(
                                        child: Text("Vault progress",
                                            style: TextStyle(
                                                fontSize: 20, fontWeight: FontWeight.w800,
                                                color: Color(0xFF101828))),
                                      ),
                                      Text(
                                        "${(progress * 100).toStringAsFixed(1)}%",
                                        style: const TextStyle(
                                            fontWeight: FontWeight.w800,
                                            color: Color(0xFF1F7A4C),
                                            fontSize: 16),
                                      ),
                                    ],
                                  ),
                                  const SizedBox(height: 8),
                                  Text("Lobby #${lobby?.lobbyId ?? "-"}",
                                      style: const TextStyle(color: Color(0xFF667085))),
                                  const SizedBox(height: 16),
                                  ClipRRect(
                                    borderRadius: BorderRadius.circular(999),
                                    child: LinearProgressIndicator(
                                        value: progress, minHeight: 14),
                                  ),
                                  const SizedBox(height: 12),
                                  Text(
                                    "₦${lobby?.currentItemAmount ?? 0} / ₦${lobby?.targetItemAmount ?? 0}",
                                    style: const TextStyle(
                                        fontSize: 18, fontWeight: FontWeight.w800,
                                        color: Color(0xFF101828)),
                                  ),
                                  if (progress >= 1.0) ...[
                                    const SizedBox(height: 12),
                                    _successBox("🎉 Vault target reached! This batch has been triggered."),
                                  ],
                                ],
                              ),
                            ),
                          ),

                          const SizedBox(height: 18),

                          // My account + Lobby access
                          Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Expanded(
                                child: Card(
                                  elevation: 0,
                                  child: Padding(
                                    padding: const EdgeInsets.all(22),
                                    child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        const Text("My account",
                                            style: TextStyle(
                                                fontSize: 20, fontWeight: FontWeight.w800,
                                                color: Color(0xFF101828))),
                                        const SizedBox(height: 16),
                                        // ── User profile card ──────────────────
                                        Container(
                                          padding: const EdgeInsets.all(14),
                                          decoration: BoxDecoration(
                                            color: const Color(0xFFF9FAFB),
                                            borderRadius: BorderRadius.circular(16),
                                            border: Border.all(color: const Color(0xFFE4E7EC)),
                                          ),
                                          child: Row(
                                            children: [
                                              Container(
                                                width: 46, height: 46,
                                                decoration: BoxDecoration(
                                                  color: const Color(0xFF1F7A4C),
                                                  borderRadius: BorderRadius.circular(14),
                                                ),
                                                child: Center(
                                                  child: Text(
                                                    accountEmail.isNotEmpty ? accountEmail[0].toUpperCase() : "?",
                                                    style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w800),
                                                  ),
                                                ),
                                              ),
                                              const SizedBox(width: 14),
                                              Expanded(
                                                child: Column(
                                                  crossAxisAlignment: CrossAxisAlignment.start,
                                                  children: [
                                                    Text(
                                                      accountEmail,
                                                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14, color: Color(0xFF101828)),
                                                      overflow: TextOverflow.ellipsis,
                                                    ),
                                                    const SizedBox(height: 4),
                                                    Wrap(
                                                      spacing: 6, runSpacing: 4,
                                                      children: [
                                                        Container(
                                                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                                          decoration: BoxDecoration(
                                                            color: isVerified ? const Color(0xFFECFDF3) : const Color(0xFFFEF3F2),
                                                            borderRadius: BorderRadius.circular(999),
                                                            border: Border.all(color: isVerified ? const Color(0xFF4BB543) : const Color(0xFFFECACA)),
                                                          ),
                                                          child: Text(
                                                            isVerified ? "✅ PAU Verified" : "❌ Not verified",
                                                            style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: isVerified ? const Color(0xFF027A48) : const Color(0xFFB42318)),
                                                          ),
                                                        ),
                                                        if (isAdmin)
                                                          Container(
                                                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                                            decoration: BoxDecoration(
                                                              color: const Color(0xFFF4F3FF),
                                                              borderRadius: BorderRadius.circular(999),
                                                              border: Border.all(color: const Color(0xFF9E77ED)),
                                                            ),
                                                            child: const Text("👑 Admin", style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: Color(0xFF5925DC))),
                                                          ),
                                                      ],
                                                    ),
                                                    if (studentEmail != null) ...[
                                                      const SizedBox(height: 4),
                                                      Text("🎓 $studentEmail", style: const TextStyle(fontSize: 12, color: Color(0xFF667085)), overflow: TextOverflow.ellipsis),
                                                    ],
                                                  ],
                                                ),
                                              ),
                                            ],
                                          ),
                                        ),
                                        const SizedBox(height: 16),
                                        SizedBox(
                                          width: double.infinity,
                                          child: ElevatedButton(
                                            onPressed: isBusy ? null : openVerifyScreen,
                                            child: Text(isVerified
                                                ? "Update PAU verification"
                                                : "Verify PAU email"),
                                          ),
                                        ),
                                        if (isAdmin) ...[
                                          const SizedBox(height: 10),
                                          SizedBox(
                                            width: double.infinity,
                                            child: OutlinedButton.icon(
                                              onPressed: isBusy ? null : openAdminDashboard,
                                              icon: const Icon(Icons.admin_panel_settings_outlined),
                                              label: const Text("Admin dashboard"),
                                            ),
                                          ),
                                        ],
                                      ],
                                    ),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 18),
                              Expanded(
                                child: Card(
                                  elevation: 0,
                                  child: Padding(
                                    padding: const EdgeInsets.all(22),
                                    child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        const Text("Lobby access",
                                            style: TextStyle(
                                                fontSize: 20, fontWeight: FontWeight.w800,
                                                color: Color(0xFF101828))),
                                        const SizedBox(height: 8),
                                        const Text(
                                            "Pay the entry fee, join, and manage your spot.",
                                            style: TextStyle(color: Color(0xFF667085))),
                                        const SizedBox(height: 16),
                                        Wrap(
                                          spacing: 10, runSpacing: 10,
                                          children: [
                                            detailTile("Lobby ID", "#${lobby?.lobbyId ?? "-"}"),
                                            detailTile("Status", lobby?.status ?? "Unknown"),
                                            detailTile("Entry fee", "₦$entryFeeAmount"),
                                          ],
                                        ),
                                        const SizedBox(height: 16),
                                        if (!isVerified)
                                          _warningBox("Verify your PAU email before paying to join.")
                                        else if (hasJoined)
                                          _successBox("✅ You're in the lobby. Add items and pay for them.")
                                        else if (hasPendingPayment || pendingPaymentReference != null)
                                          Column(
                                            crossAxisAlignment: CrossAxisAlignment.start,
                                            children: [
                                              _infoBox(
                                                "⏳ Entry fee payment pending.${pendingPaymentReference != null ? "\nRef: $pendingPaymentReference" : ""}",
                                              ),
                                              const SizedBox(height: 10),
                                              SizedBox(
                                                width: double.infinity,
                                                child: ElevatedButton.icon(
                                                  onPressed: isBusy ? null : verifyEntryFeePayment,
                                                  icon: const Icon(Icons.verified_outlined),
                                                  label: const Text("I've Completed Payment"),
                                                ),
                                              ),
                                              const SizedBox(height: 8),
                                              SizedBox(
                                                width: double.infinity,
                                                child: OutlinedButton.icon(
                                                  onPressed: isBusy ? null : reopenPaymentLink,
                                                  icon: const Icon(Icons.open_in_new),
                                                  label: const Text("Open payment page"),
                                                ),
                                              ),
                                            ],
                                          )
                                        else
                                          SizedBox(
                                            width: double.infinity,
                                            child: ElevatedButton.icon(
                                              onPressed: isBusy ? null : startEntryFeePayment,
                                              icon: const Icon(Icons.payment),
                                              label: Text("Pay ₦$entryFeeAmount to Join"),
                                            ),
                                          ),
                                        const SizedBox(height: 10),
                                        SizedBox(
                                          width: double.infinity,
                                          child: OutlinedButton(
                                            onPressed: isBusy || !hasJoined ? null : leaveLobby,
                                            child: const Text("Leave lobby"),
                                          ),
                                        ),
                                      ],
                                    ),
                                  ),
                                ),
                              ),
                            ],
                          ),

                          const SizedBox(height: 18),

                          // Add item
                          Card(
                            elevation: 0,
                            child: Padding(
                              padding: const EdgeInsets.all(22),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  const Text("Add item",
                                      style: TextStyle(
                                          fontSize: 20, fontWeight: FontWeight.w800,
                                          color: Color(0xFF101828))),
                                  const SizedBox(height: 6),
                                  const Text(
                                      "Paste your product link and the guest price. Pay for it to lock it into the vault.",
                                      style: TextStyle(color: Color(0xFF667085))),
                                  const SizedBox(height: 14),

                                  // ── Guest price notice ────────────────────────────────
                                  Container(
                                    padding: const EdgeInsets.all(14),
                                    decoration: BoxDecoration(
                                      color: const Color(0xFFFFF7E6),
                                      borderRadius: BorderRadius.circular(14),
                                      border: Border.all(color: const Color(0xFFFAC515)),
                                    ),
                                    child: const Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Row(
                                          children: [
                                            Icon(Icons.warning_amber_rounded,
                                                size: 18, color: Color(0xFFB54708)),
                                            SizedBox(width: 8),
                                            Text("Use guest account (logged out account) pricing only",
                                                style: TextStyle(
                                                    fontWeight: FontWeight.w800,
                                                    fontSize: 14,
                                                    color: Color(0xFF7A2E0E))),
                                          ],
                                        ),
                                        SizedBox(height: 8),
                                        Text(
                                          "UniCart standardises all prices to non-personalised (guest account) pricing."

                                          "Before submitting your item:"
                                          "  • LOG OUT of Temu (or any other platform)"
                                          "  • Enter the price you see on guest account"

                                          "• Submitting a personalised or discounted price that differs from "
                                          "the standard guest price is considered a pricing violation and "
                                          "may result in your item being forcefully removed with no refund.",
                                          style: TextStyle(
                                              fontSize: 13,
                                              color: Color(0xFF7A2E0E),
                                              height: 1.5),
                                        ),
                                      ],
                                    ),
                                  ),

                                  const SizedBox(height: 14),

                                  // ── No-refund notice ──────────────────────────────────
                                  Container(
                                    padding: const EdgeInsets.all(12),
                                    decoration: BoxDecoration(
                                      color: const Color(0xFFFEF3F2),
                                      borderRadius: BorderRadius.circular(12),
                                      border: Border.all(color: const Color(0xFFFECACA)),
                                    ),
                                    child: const Row(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Icon(Icons.gavel_outlined,
                                            size: 16, color: Color(0xFFB42318)),
                                        SizedBox(width: 8),
                                        Expanded(
                                          child: Text(
                                            "No-Refund Policy: Once your item payment is submitted, "
                                            "it is non-refundable. Items removed for pricing violations "
                                            "or fraud will not be refunded under any circumstance.",
                                            style: TextStyle(
                                                fontSize: 12,
                                                color: Color(0xFFB42318),
                                                fontWeight: FontWeight.w600,
                                                height: 1.5),
                                          ),
                                        ),
                                      ],
                                    ),
                                  ),

                                  const SizedBox(height: 16),
                                  TextField(
                                    controller: itemLinkController,
                                    decoration: const InputDecoration(
                                      labelText: "Product link",
                                      prefixIcon: Icon(Icons.link),
                                      helperText: "Copy the link from a guest / logged-out browser session",
                                    ),
                                  ),
                                  const SizedBox(height: 12),
                                  TextField(
                                    controller: itemAmountController,
                                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                                    decoration: const InputDecoration(
                                      labelText: "Guest price (₦)",
                                      prefixIcon: Icon(Icons.payments_outlined),
                                      helperText: "Price shown to logged-out visitors only",
                                    ),
                                  ),
                                  const SizedBox(height: 14),
                                  SizedBox(
                                    width: double.infinity,
                                    child: ElevatedButton.icon(
                                      onPressed: isBusy || !hasJoined ? null : addItem,
                                      icon: const Icon(Icons.add),
                                      label: const Text("Add item"),
                                    ),
                                  ),
                                  if (!hasJoined)
                                    Padding(
                                      padding: const EdgeInsets.only(top: 8),
                                      child: Text(
                                        "Join the lobby first to add items.",
                                        style: TextStyle(
                                            color: Colors.grey.shade500, fontSize: 12),
                                      ),
                                    ),
                                ],
                              ),
                            ),
                          ),

                          const SizedBox(height: 18),

                          // My active items
                          Card(
                            elevation: 0,
                            child: Padding(
                              padding: const EdgeInsets.all(22),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      const Expanded(
                                        child: Text("My active items",
                                            style: TextStyle(
                                                fontSize: 20, fontWeight: FontWeight.w800,
                                                color: Color(0xFF101828))),
                                      ),
                                      if (activeItems.isNotEmpty)
                                        infoPill("${activeItems.length} item${activeItems.length == 1 ? "" : "s"}"),
                                    ],
                                  ),
                                  const SizedBox(height: 6),
                                  const Text(
                                      "Pay to lock items in. Unpaid items are removed when the batch triggers.",
                                      style: TextStyle(color: Color(0xFF667085), fontSize: 13)),
                                  const SizedBox(height: 14),
                                  if (activeItems.isEmpty)
                                    _emptyState(
                                      "No active items yet.\nAdd an item above and pay for it to lock it in.",
                                      icon: Icons.shopping_bag_outlined,
                                    )
                                  else
                                    ...activeItems.map((item) {
                                      final itemId = item["item_id"] as int;
                                      final itemLink = item["item_link"]?.toString() ?? "";
                                      final itemAmount = item["item_amount"] ?? 0;
                                      final paymentStatus =
                                          item["item_payment_status"]?.toString() ?? "unpaid";
                                      final isLocked = item["is_locked"] == true;
                                      final isPaid = item["is_paid"] == true;
                                      final paymentReference =
                                          item["item_payment_reference"]?.toString();

                                      return Container(
                                        margin: const EdgeInsets.only(bottom: 12),
                                        padding: const EdgeInsets.all(16),
                                        decoration: BoxDecoration(
                                          color: isPaid
                                              ? const Color(0xFFF0FDF4)
                                              : paymentStatus == "pending"
                                                  ? const Color(0xFFFFFBEB)
                                                  : const Color(0xFFF9FAFB),
                                          borderRadius: BorderRadius.circular(18),
                                          border: Border.all(
                                            color: isPaid
                                                ? const Color(0xFF86EFAC)
                                                : paymentStatus == "pending"
                                                    ? const Color(0xFFFDE68A)
                                                    : const Color(0xFFE4E7EC),
                                          ),
                                        ),
                                        child: Column(
                                          crossAxisAlignment: CrossAxisAlignment.start,
                                          children: [
                                            Text(itemLink,
                                                style: const TextStyle(
                                                    fontWeight: FontWeight.w700,
                                                    color: Color(0xFF101828)),
                                                maxLines: 2,
                                                overflow: TextOverflow.ellipsis),
                                            const SizedBox(height: 10),
                                            Wrap(
                                              spacing: 8, runSpacing: 8,
                                              children: [
                                                infoPill("₦$itemAmount"),
                                                infoPill("ID: $itemId"),
                                                buildItemPaymentBadge(paymentStatus),
                                              ],
                                            ),
                                            const SizedBox(height: 12),
                                            Wrap(
                                              spacing: 8, runSpacing: 8,
                                              alignment: WrapAlignment.end,
                                              children: [
                                                if (!isLocked)
                                                  OutlinedButton.icon(
                                                    onPressed: isBusy
                                                        ? null
                                                        : () => payForItem(itemId),
                                                    icon: const Icon(Icons.payment_outlined, size: 16),
                                                    label: const Text("Pay for item"),
                                                  ),
                                                if (paymentStatus == "pending" &&
                                                    paymentReference != null)
                                                  ElevatedButton.icon(
                                                    onPressed: isBusy
                                                        ? null
                                                        : () => verifyItemPayment(paymentReference),
                                                    icon: const Icon(Icons.verified_outlined, size: 16),
                                                    label: const Text("Verify payment"),
                                                  ),
                                                if (!isLocked)
                                                  OutlinedButton.icon(
                                                    onPressed: isBusy
                                                        ? null
                                                        : () => removeItem(itemId),
                                                    icon: const Icon(Icons.delete_outline, size: 16),
                                                    label: const Text("Remove"),
                                                  ),
                                                if (isPaid)
                                                  Container(
                                                    padding: const EdgeInsets.symmetric(
                                                        horizontal: 12, vertical: 8),
                                                    decoration: BoxDecoration(
                                                      color: const Color(0xFFECFDF3),
                                                      borderRadius: BorderRadius.circular(999),
                                                      border: Border.all(
                                                          color: const Color(0xFF4BB543)),
                                                    ),
                                                    child: const Row(
                                                      mainAxisSize: MainAxisSize.min,
                                                      children: [
                                                        Icon(Icons.lock_outline,
                                                            size: 14, color: Color(0xFF027A48)),
                                                        SizedBox(width: 4),
                                                        Text("Locked",
                                                            style: TextStyle(
                                                                color: Color(0xFF027A48),
                                                                fontWeight: FontWeight.w700,
                                                                fontSize: 13)),
                                                      ],
                                                    ),
                                                  ),
                                              ],
                                            ),
                                          ],
                                        ),
                                      );
                                    }),
                                ],
                              ),
                            ),
                          ),

                          const SizedBox(height: 18),

                          // Batch history
                          Card(
                            elevation: 0,
                            child: Padding(
                              padding: const EdgeInsets.all(22),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  const Text("My batch history",
                                      style: TextStyle(
                                          fontSize: 20, fontWeight: FontWeight.w800,
                                          color: Color(0xFF101828))),
                                  const SizedBox(height: 6),
                                  const Text(
                                      "Track older lobbies you contributed to.",
                                      style: TextStyle(color: Color(0xFF667085))),
                                  const SizedBox(height: 14),
                                  if (historyBatches.isEmpty)
                                    _emptyState(
                                      "No batch history yet.\nYour completed batches will appear here.",
                                      icon: Icons.history_outlined,
                                    )
                                  else
                                    ...historyBatches.map((batch) {
                                      final batchItems = ((batch["items"] as List?)
                                                  ?.cast<Map<String, dynamic>>()) ??
                                              [];
                                      final rawStatus = batch["status"]?.toString() ?? "";

                                      return Container(
                                        margin: const EdgeInsets.only(bottom: 14),
                                        decoration: BoxDecoration(
                                          color: const Color(0xFFF9FAFB),
                                          borderRadius: BorderRadius.circular(20),
                                          border: Border.all(color: const Color(0xFFE4E7EC)),
                                        ),
                                        child: ExpansionTile(
                                          tilePadding: const EdgeInsets.symmetric(
                                              horizontal: 18, vertical: 8),
                                          childrenPadding:
                                              const EdgeInsets.fromLTRB(18, 0, 18, 18),
                                          title: Text("Batch #${batch["lobby_id"]}",
                                              style: const TextStyle(
                                                  fontWeight: FontWeight.w800,
                                                  color: Color(0xFF101828))),
                                          subtitle: Padding(
                                            padding: const EdgeInsets.only(top: 6),
                                            child: Text(
                                                "${prettyBatchStatus(rawStatus)} • My paid total: ₦${batch["my_total_item_amount"]}"),
                                          ),
                                          children: [
                                            Align(
                                                alignment: Alignment.centerLeft,
                                                child: buildStatusBadge(rawStatus)),
                                            const SizedBox(height: 10),
                                            Align(
                                              alignment: Alignment.centerLeft,
                                              child: Text(
                                                batchStatusDescription(rawStatus),
                                                style: const TextStyle(
                                                    color: Color(0xFF475467),
                                                    fontWeight: FontWeight.w600),
                                              ),
                                            ),
                                            const SizedBox(height: 12),
                                            buildBatchProgressRow(rawStatus),
                                            const SizedBox(height: 12),
                                            Wrap(
                                              spacing: 10, runSpacing: 10,
                                              children: [
                                                infoPill(
                                                    "Final: ₦${batch["final_item_amount"]} / ₦${batch["target_item_amount"]}"),
                                                infoPill("Members: ${batch["member_count"]}"),
                                                infoPill("My items: ${batch["my_item_count"]}"),
                                              ],
                                            ),
                                            const SizedBox(height: 12),
                                            if (batchItems.isEmpty)
                                              const Text("No items for this batch.",
                                                  style: TextStyle(color: Color(0xFF667085)))
                                            else
                                              ...batchItems.map((item) => Container(
                                                    margin: const EdgeInsets.only(bottom: 8),
                                                    padding: const EdgeInsets.all(14),
                                                    decoration: BoxDecoration(
                                                      color: Colors.white,
                                                      borderRadius: BorderRadius.circular(14),
                                                      border: Border.all(
                                                          color: const Color(0xFFE4E7EC)),
                                                    ),
                                                    child: Column(
                                                      crossAxisAlignment: CrossAxisAlignment.start,
                                                      children: [
                                                        Text(
                                                          item["item_link"]?.toString() ?? "",
                                                          style: const TextStyle(
                                                              fontWeight: FontWeight.w700,
                                                              color: Color(0xFF101828)),
                                                          maxLines: 2,
                                                          overflow: TextOverflow.ellipsis,
                                                        ),
                                                        const SizedBox(height: 8),
                                                        Wrap(
                                                          spacing: 8, runSpacing: 8,
                                                          children: [
                                                            infoPill("₦${item["item_amount"] ?? 0}"),
                                                            buildItemPaymentBadge(
                                                              item["item_payment_status"]
                                                                      ?.toString() ??
                                                                  "unpaid",
                                                            ),
                                                          ],
                                                        ),
                                                      ],
                                                    ),
                                                  )),
                                          ],
                                        ),
                                      );
                                    }),
                                ],
                              ),
                            ),
                          ),

                          const SizedBox(height: 32),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
    );
  }
}