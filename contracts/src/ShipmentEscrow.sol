// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {SignatureChecker} from "@openzeppelin/contracts/utils/cryptography/SignatureChecker.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IIntakeGate { function intakePaused() external view returns (bool); }

/// @notice Merchant-funded compensation reserve. AI has no authority over this contract.
/// @dev Immutable per case. Currency units are the allowlisted token's smallest units.
contract ShipmentEscrow is EIP712, ReentrancyGuard {
    using SafeERC20 for IERC20;

    enum State { CREATED, FUNDED, ACTIVE, DISPUTED, RESOLVED, CANCELLED }
    struct Terms {
        bytes32 caseId;
        address merchant;
        address buyer;
        address token;
        uint256 reserveAmount;
        uint64 acceptBy;
        uint64 reviewDeadline;
        address arbitrationSafe;
        bytes32 termsHash;
    }
    struct ReportApproval {
        bytes32 reportHash;
        bytes32 evidenceRoot;
        uint256 version;
        uint256 nonce;
        uint64 expiresAt;
    }
    struct Settlement {
        bytes32 reportHash;
        bytes32 evidenceRoot;
        uint256 reportVersion;
        uint256 merchantAmount;
        uint256 buyerAmount;
        uint256 nonce;
        uint64 expiresAt;
    }
    struct Resolution {
        bytes32 evidenceRoot;
        bytes32 decisionHash;
        uint256 merchantAmount;
        uint256 buyerAmount;
        uint256 nonce;
        uint64 expiresAt;
    }

    bytes32 public constant TERMS_TYPEHASH = keccak256("TermsAcceptance(bytes32 caseId,address merchant,address buyer,address token,uint256 reserveAmount,uint64 acceptBy,uint64 reviewDeadline,address arbitrationSafe,bytes32 termsHash,bytes32 dispatchHash,uint64 expiresAt)");
    bytes32 public constant REPORT_TYPEHASH = keccak256("ReportApproval(bytes32 caseId,bytes32 reportHash,bytes32 evidenceRoot,uint256 version,uint256 nonce,uint64 expiresAt)");
    bytes32 public constant SETTLEMENT_TYPEHASH = keccak256("Settlement(bytes32 caseId,bytes32 reportHash,bytes32 evidenceRoot,uint256 reportVersion,uint256 merchantAmount,uint256 buyerAmount,uint256 nonce,uint64 expiresAt)");
    bytes32 public constant RESOLUTION_TYPEHASH = keccak256("DisputeResolution(bytes32 caseId,bytes32 evidenceRoot,bytes32 decisionHash,uint256 merchantAmount,uint256 buyerAmount,uint256 nonce,uint64 expiresAt)");

    Terms public terms;
    address public immutable factory;
    State public state;
    bytes32 public dispatchHash;
    bytes32 public receiptHash;
    bytes32 public evidenceRoot;
    uint256 public evidenceSequence;
    bytes32 public acceptedReportHash;
    uint256 public acceptedReportVersion;
    uint256 public reportNonce;
    uint256 public settlementNonce;
    uint256 public disputeNonce;
    uint256 public totalFunded;
    uint256 public totalWithdrawn;
    mapping(address => uint256) public claimable;

    error Unauthorized();
    error InvalidState();
    error InvalidTerms();
    error InvalidCommitment();
    error SignatureInvalid();
    error SignatureExpired();
    error NonceMismatch();
    error EvidenceMismatch();
    error InvalidAllocation();
    error DeadlinePassed();
    error DeadlineNotReached();
    error IntakePaused();
    error UnsupportedTokenBehavior();
    error NothingToWithdraw();

    event Funded(address indexed payer, uint256 amount);
    event EvidenceCommitted(uint8 indexed stage, bytes32 indexed manifestHash, bytes32 evidenceRoot, uint256 sequence, address actor);
    event Activated(bytes32 termsHash);
    event ReportAccepted(bytes32 indexed reportHash, uint256 version, bytes32 evidenceRoot, uint256 nonce);
    event DisputeOpened(address indexed actor, bytes32 indexed evidenceHash);
    event Resolved(uint256 merchantAmount, uint256 buyerAmount, bytes32 indexed commitment, bool arbitrated);
    event Cancelled(uint256 refunded);
    event Withdrawn(address indexed beneficiary, uint256 amount);

    constructor(Terms memory t) EIP712("KienChungEscrow", "1") {
        if (t.caseId == bytes32(0) || t.merchant == address(0) || t.buyer == address(0)
            || t.merchant == t.buyer || t.token.code.length == 0 || t.reserveAmount == 0
            || t.acceptBy <= block.timestamp || t.reviewDeadline <= t.acceptBy
            || t.arbitrationSafe.code.length == 0 || t.termsHash == bytes32(0)) revert InvalidTerms();
        terms = t;
        factory = msg.sender;
    }

    modifier party() {
        if (msg.sender != terms.merchant && msg.sender != terms.buyer) revert Unauthorized();
        _;
    }
    modifier intakeOpen() {
        if (IIntakeGate(factory).intakePaused()) revert IntakePaused();
        _;
    }

    function fund() external nonReentrant intakeOpen {
        if (state != State.CREATED) revert InvalidState();
        if (msg.sender != terms.merchant) revert Unauthorized();
        if (block.timestamp > terms.acceptBy) revert DeadlinePassed();
        IERC20 token = IERC20(terms.token);
        uint256 beforeBalance = token.balanceOf(address(this));
        token.safeTransferFrom(msg.sender, address(this), terms.reserveAmount);
        if (token.balanceOf(address(this)) - beforeBalance != terms.reserveAmount) revert UnsupportedTokenBehavior();
        totalFunded = terms.reserveAmount;
        state = State.FUNDED;
        emit Funded(msg.sender, totalFunded);
    }

    function commitDispatch(bytes32 manifestHash) external {
        if (msg.sender != terms.merchant) revert Unauthorized();
        if (state != State.CREATED && state != State.FUNDED) revert InvalidState();
        if (block.timestamp > terms.acceptBy) revert DeadlinePassed();
        if (dispatchHash != bytes32(0)) revert InvalidCommitment();
        dispatchHash = manifestHash;
        _append(0, manifestHash);
    }

    function activate(uint64 expiresAt, bytes calldata merchantSignature, bytes calldata buyerSignature) external intakeOpen {
        if (state != State.FUNDED) revert InvalidState();
        if (block.timestamp > terms.acceptBy) revert DeadlinePassed();
        if (dispatchHash == bytes32(0)) revert InvalidCommitment();
        _notExpired(expiresAt);
        _both(termsDigest(expiresAt), merchantSignature, buyerSignature);
        state = State.ACTIVE;
        emit Activated(terms.termsHash);
    }

    function commitReceipt(bytes32 manifestHash) external {
        if (msg.sender != terms.buyer) revert Unauthorized();
        if (state != State.ACTIVE && state != State.DISPUTED) revert InvalidState();
        if (state == State.ACTIVE && block.timestamp > terms.reviewDeadline) revert DeadlinePassed();
        if (receiptHash != bytes32(0)) revert InvalidCommitment();
        receiptHash = manifestHash;
        _append(1, manifestHash);
    }

    function appendEvidence(bytes32 manifestHash) external party {
        if (state != State.ACTIVE && state != State.DISPUTED) revert InvalidState();
        if (state == State.ACTIVE && block.timestamp > terms.reviewDeadline) revert DeadlinePassed();
        _append(2, manifestHash);
    }

    function _append(uint8 stage, bytes32 manifestHash) private {
        if (manifestHash == bytes32(0)) revert InvalidCommitment();
        evidenceRoot = keccak256(abi.encode(evidenceRoot, stage, manifestHash, ++evidenceSequence, msg.sender));
        // New evidence invalidates all pending report, settlement and arbitration signatures.
        acceptedReportHash = bytes32(0);
        ++reportNonce;
        ++settlementNonce;
        ++disputeNonce;
        emit EvidenceCommitted(stage, manifestHash, evidenceRoot, evidenceSequence, msg.sender);
    }

    function acceptReport(ReportApproval calldata approval, bytes calldata merchantSignature, bytes calldata buyerSignature) external {
        _reviewState();
        _notExpired(approval.expiresAt);
        if (receiptHash == bytes32(0) || approval.reportHash == bytes32(0) || approval.version <= acceptedReportVersion) revert InvalidCommitment();
        if (approval.evidenceRoot != evidenceRoot) revert EvidenceMismatch();
        if (approval.nonce != reportNonce) revert NonceMismatch();
        _both(reportDigest(approval), merchantSignature, buyerSignature);
        acceptedReportHash = approval.reportHash;
        acceptedReportVersion = approval.version;
        emit ReportAccepted(approval.reportHash, approval.version, evidenceRoot, reportNonce++);
        ++settlementNonce;
    }

    function settle(Settlement calldata allocation, bytes calldata merchantSignature, bytes calldata buyerSignature) external nonReentrant {
        _reviewState();
        _notExpired(allocation.expiresAt);
        if (allocation.reportHash == bytes32(0) || allocation.reportHash != acceptedReportHash
            || allocation.reportVersion != acceptedReportVersion) revert InvalidCommitment();
        if (allocation.evidenceRoot != evidenceRoot) revert EvidenceMismatch();
        if (allocation.nonce != settlementNonce) revert NonceMismatch();
        _allocation(allocation.merchantAmount, allocation.buyerAmount);
        _both(settlementDigest(allocation), merchantSignature, buyerSignature);
        ++settlementNonce;
        _resolve(allocation.merchantAmount, allocation.buyerAmount, allocation.reportHash, false);
    }

    function openDispute(bytes32 evidenceHash) external party {
        if (state != State.ACTIVE) revert InvalidState();
        if (block.timestamp > terms.reviewDeadline) revert DeadlinePassed();
        if (evidenceHash == bytes32(0)) revert InvalidCommitment();
        state = State.DISPUTED;
        // An agreement signed before a dispute cannot silently settle the changed state.
        ++settlementNonce;
        ++disputeNonce;
        emit DisputeOpened(msg.sender, evidenceHash);
    }

    function resolveDispute(Resolution calldata resolution, bytes calldata safeSignature) external nonReentrant {
        if (state != State.DISPUTED) revert InvalidState();
        _notExpired(resolution.expiresAt);
        if (resolution.decisionHash == bytes32(0)) revert InvalidCommitment();
        if (resolution.evidenceRoot != evidenceRoot) revert EvidenceMismatch();
        if (resolution.nonce != disputeNonce) revert NonceMismatch();
        _allocation(resolution.merchantAmount, resolution.buyerAmount);
        if (!SignatureChecker.isValidSignatureNow(terms.arbitrationSafe, resolutionDigest(resolution), safeSignature)) revert SignatureInvalid();
        ++disputeNonce;
        _resolve(resolution.merchantAmount, resolution.buyerAmount, resolution.decisionHash, true);
    }

    function closeExpiredUncontested() external {
        if (state != State.ACTIVE) revert InvalidState();
        if (block.timestamp <= terms.reviewDeadline) revert DeadlineNotReached();
        _resolve(terms.reserveAmount, 0, terms.termsHash, false);
    }

    function cancelUnactivated() external {
        if (state != State.CREATED && state != State.FUNDED) revert InvalidState();
        if (block.timestamp <= terms.acceptBy && !(state == State.CREATED && msg.sender == terms.merchant)) revert DeadlineNotReached();
        state = State.CANCELLED;
        claimable[terms.merchant] = totalFunded;
        emit Cancelled(totalFunded);
    }

    function withdraw() external nonReentrant {
        uint256 amount = claimable[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        claimable[msg.sender] = 0;
        totalWithdrawn += amount;
        // A failed token transfer reverts these updates, preserving the withdrawal right.
        IERC20(terms.token).safeTransfer(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    function unallocated() external view returns (uint256) {
        return state == State.FUNDED || state == State.ACTIVE || state == State.DISPUTED ? totalFunded : 0;
    }

    function _reviewState() private view {
        if (state != State.ACTIVE && state != State.DISPUTED) revert InvalidState();
        if (state == State.ACTIVE && block.timestamp > terms.reviewDeadline) revert DeadlinePassed();
    }
    function _notExpired(uint64 expiresAt) private view {
        if (block.timestamp > expiresAt) revert SignatureExpired();
    }
    function _both(bytes32 digest, bytes memory m, bytes memory b) private view {
        if (!SignatureChecker.isValidSignatureNow(terms.merchant, digest, m)
            || !SignatureChecker.isValidSignatureNow(terms.buyer, digest, b)) revert SignatureInvalid();
    }
    function _allocation(uint256 m, uint256 b) private view {
        if (m > terms.reserveAmount || b != terms.reserveAmount - m) revert InvalidAllocation();
    }
    function _resolve(uint256 m, uint256 b, bytes32 commitment, bool arbitrated) private {
        state = State.RESOLVED;
        claimable[terms.merchant] = m;
        claimable[terms.buyer] = b;
        emit Resolved(m, b, commitment, arbitrated);
    }

    function termsDigest(uint64 expiresAt) public view returns (bytes32) {
        Terms memory t = terms;
        return _hashTypedDataV4(keccak256(abi.encode(TERMS_TYPEHASH, t.caseId, t.merchant, t.buyer,
            t.token, t.reserveAmount, t.acceptBy, t.reviewDeadline, t.arbitrationSafe, t.termsHash, dispatchHash, expiresAt)));
    }
    function reportDigest(ReportApproval memory a) public view returns (bytes32) {
        return _hashTypedDataV4(keccak256(abi.encode(REPORT_TYPEHASH, terms.caseId, a.reportHash,
            a.evidenceRoot, a.version, a.nonce, a.expiresAt)));
    }
    function settlementDigest(Settlement memory a) public view returns (bytes32) {
        return _hashTypedDataV4(keccak256(abi.encode(SETTLEMENT_TYPEHASH, terms.caseId, a.reportHash,
            a.evidenceRoot, a.reportVersion, a.merchantAmount, a.buyerAmount, a.nonce, a.expiresAt)));
    }
    function resolutionDigest(Resolution memory a) public view returns (bytes32) {
        return _hashTypedDataV4(keccak256(abi.encode(RESOLUTION_TYPEHASH, terms.caseId,
            a.evidenceRoot, a.decisionHash, a.merchantAmount, a.buyerAmount, a.nonce, a.expiresAt)));
    }
}
