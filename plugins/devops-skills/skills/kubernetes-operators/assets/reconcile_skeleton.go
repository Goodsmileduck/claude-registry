// Reconcile skeleton — passes reconcile_lint.py.
// Substitute the placeholder type / module path before use.
package controllers

import (
	"context"
	"fmt"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/predicate"

	appsv1alpha1 "<MODULE>/api/v1alpha1"
)

const (
	finalizer    = "<group>/finalizer"
	resyncPeriod = 5 * time.Minute
)

// Reconciler watches MyApp resources.
type Reconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

func (r *Reconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&appsv1alpha1.MyApp{}).
		Owns(&appsv1.Deployment{}).
		WithEventFilter(predicate.GenerationChangedPredicate{}).
		Complete(r)
}

func (r *Reconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx).WithValues("myapp", req.NamespacedName)

	var cr appsv1alpha1.MyApp
	if err := r.Get(ctx, req.NamespacedName, &cr); err != nil {
		if apierrors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, fmt.Errorf("fetch CR: %w", err)
	}

	// Deletion path.
	if !cr.DeletionTimestamp.IsZero() {
		return r.handleDelete(ctx, &cr)
	}

	// Ensure finalizer before touching external state.
	if !controllerutil.ContainsFinalizer(&cr, finalizer) {
		controllerutil.AddFinalizer(&cr, finalizer)
		if err := r.Update(ctx, &cr); err != nil {
			return ctrl.Result{}, fmt.Errorf("install finalizer: %w", err)
		}
		return ctrl.Result{Requeue: true}, nil
	}

	// Converge once. Any error returned drives a backoff requeue via ctrl-runtime.
	result, recErr := r.converge(ctx, &cr)
	r.applyCondition(&cr, recErr)
	cr.Status.ObservedGeneration = cr.Generation

	if err := r.Status().Update(ctx, &cr); err != nil {
		logger.Error(err, "status update failed")
		if recErr == nil {
			return ctrl.Result{}, err
		}
	}
	return result, recErr
}

// converge brings child resources into the state declared by cr.Spec.
// It must be idempotent: running it twice with no spec change is a no-op.
func (r *Reconciler) converge(ctx context.Context, cr *appsv1alpha1.MyApp) (ctrl.Result, error) {
	dep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      cr.Name,
			Namespace: cr.Namespace,
		},
	}
	if _, err := controllerutil.CreateOrUpdate(ctx, r.Client, dep, func() error {
		replicas := int32(cr.Spec.Replicas)
		dep.Spec.Replicas = &replicas
		// dep.Spec.Template.Spec.Containers = buildContainers(cr)  // implement separately
		return controllerutil.SetControllerReference(cr, dep, r.Scheme)
	}); err != nil {
		return ctrl.Result{}, fmt.Errorf("ensure deployment: %w", err)
	}
	// Periodic resync keeps status fresh even when nothing changes.
	return ctrl.Result{RequeueAfter: resyncPeriod}, nil
}

func (r *Reconciler) applyCondition(cr *appsv1alpha1.MyApp, recErr error) {
	cond := metav1.Condition{
		Type:               "Ready",
		ObservedGeneration: cr.Generation,
	}
	if recErr == nil {
		cond.Status = metav1.ConditionTrue
		cond.Reason = "Converged"
		cond.Message = "desired state matches actual"
	} else {
		cond.Status = metav1.ConditionFalse
		cond.Reason = "ConvergeFailed"
		cond.Message = recErr.Error()
	}
	meta.SetStatusCondition(&cr.Status.Conditions, cond)
}

func (r *Reconciler) handleDelete(ctx context.Context, cr *appsv1alpha1.MyApp) (ctrl.Result, error) {
	if !controllerutil.ContainsFinalizer(cr, finalizer) {
		return ctrl.Result{}, nil
	}
	if err := r.teardownExternal(ctx, cr); err != nil {
		return ctrl.Result{RequeueAfter: 30 * time.Second}, err
	}
	controllerutil.RemoveFinalizer(cr, finalizer)
	if err := r.Update(ctx, cr); err != nil {
		return ctrl.Result{}, fmt.Errorf("clear finalizer: %w", err)
	}
	return ctrl.Result{}, nil
}

// teardownExternal removes external state owned by this CR.
func (r *Reconciler) teardownExternal(ctx context.Context, cr *appsv1alpha1.MyApp) error {
	// e.g. delete cloud-side database, S3 bucket, DNS record, etc.
	_ = ctx
	_ = cr
	return nil
}
